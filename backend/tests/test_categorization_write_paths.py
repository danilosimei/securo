"""Every path that writes `Transaction.category_id` records who did it.

One test per write path: the transaction's `category_source` and the
`CategorizationEvent` written next to it. Protected categories (a person's
choice) must survive the automatic layers.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.categorization import CategorizationEvent
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.providers.base import AccountData, TransactionData
from app.schemas.transaction import (
    InstallmentSeriesCreate,
    TransactionCreate,
    TransactionImport,
    TransactionUpdate,
    TransferCreate,
)
from app.services import category_service, rule_service, transaction_service
from app.services.connection_service import sync_connection
from app.services.import_service import import_transactions
from app.services.recurring_transaction_service import generate_pending


async def _events(session: AsyncSession, tx_id: uuid.UUID) -> list[CategorizationEvent]:
    result = await session.execute(
        select(CategorizationEvent)
        .where(CategorizationEvent.transaction_id == tx_id)
        .order_by(CategorizationEvent.created_at)
    )
    return list(result.scalars().all())


async def _reload(session: AsyncSession, tx_id: uuid.UUID) -> Transaction:
    result = await session.execute(
        select(Transaction).where(Transaction.id == tx_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _make_rule(session, user, actions, value="UBER", priority=10) -> Rule:
    rule = Rule(
        id=uuid.uuid4(),
        user_id=user.id,
        name=f"rule {value}",
        conditions_op="and",
        conditions=[{"field": "description", "op": "contains", "value": value}],
        actions=actions,
        priority=priority,
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    return rule


async def _make_manual_account(session, user, name="Carteira") -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user.id, name=name, type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.commit()
    return account


# ---------------------------------------------------------------------------
# transaction_service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_update_marks_user(session, test_user, test_workspace, test_transactions, test_categories):
    tx = test_transactions[3]  # PIX RECEBIDO, uncategorized
    target = test_categories[2].id

    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=target)
    )

    tx = await _reload(session, tx.id)
    assert tx.category_id == target
    assert tx.category_source == "user"
    events = await _events(session, tx.id)
    assert len(events) == 1
    assert events[0].source == "user"
    assert events[0].previous_category_id is None
    assert events[0].new_category_id == target
    assert events[0].user_id == test_user.id


@pytest.mark.asyncio
async def test_manual_create_with_category_marks_user(session, test_user, test_workspace, test_account, test_categories):
    tx = await transaction_service.create_transaction(
        session, test_workspace.id, test_user.id,
        TransactionCreate(
            account_id=test_account.id, description="PADARIA", amount=Decimal("10"),
            date=date.today(), type="debit", category_id=test_categories[0].id,
        ),
    )
    tx = await _reload(session, tx.id)
    assert tx.category_source == "user"
    events = await _events(session, tx.id)
    assert [e.source for e in events] == ["user"]


@pytest.mark.asyncio
async def test_installment_propagation_marks_user(session, test_user, test_workspace, test_account, test_categories):
    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="GELADEIRA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=test_categories[0].id,
            ),
            installments=3,
        ),
    )
    for tx in created:
        assert (await _reload(session, tx.id)).category_source == "user"

    target = test_categories[1].id
    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=target, apply_to="all"),
    )

    for tx in created:
        row = await _reload(session, tx.id)
        assert row.category_id == target
        assert row.category_source == "user"
        events = await _events(session, tx.id)
        assert events[-1].source == "user"
        assert events[-1].previous_category_id == test_categories[0].id
        assert events[-1].new_category_id == target


@pytest.mark.asyncio
async def test_paired_transfer_copies_source(session, test_user, test_workspace, test_account, test_categories):
    other = await _make_manual_account(session, test_user)
    debit, credit = await transaction_service.create_transfer(
        session, test_workspace.id, test_user.id,
        TransferCreate(
            from_account_id=test_account.id, to_account_id=other.id,
            description="XFER", amount=Decimal("100"), date=date.today(),
        ),
    )
    target = test_categories[0].id
    await transaction_service.update_transaction(
        session, debit.id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=target, apply_to_transfer_pair=True),
    )

    paired = await _reload(session, credit.id)
    assert paired.category_id == target
    assert paired.category_source == "user"
    events = await _events(session, credit.id)
    assert [e.source for e in events] == ["user"]
    assert events[0].new_category_id == target


@pytest.mark.asyncio
async def test_bulk_categorize_marks_user_with_events(session, test_user, test_workspace, test_transactions, test_categories):
    target = test_categories[0].id
    ids = [t.id for t in test_transactions[:4]]
    previous = {t.id: t.category_id for t in test_transactions[:4]}

    count = await transaction_service.bulk_update_category(
        session, test_workspace.id, ids, target, user_id=test_user.id
    )

    assert count == 4
    for tx_id in ids:
        row = await _reload(session, tx_id)
        assert row.category_id == target
        assert row.category_source == "user"
        events = await _events(session, tx_id)
        assert len(events) == 1
        assert events[0].source == "user"
        assert events[0].previous_category_id == previous[tx_id]
        assert events[0].user_id == test_user.id


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_on_ingest_marks_rule(session, test_user, test_workspace, test_account, test_categories):
    target = test_categories[1].id
    await _make_rule(session, test_user, [{"op": "set_category", "value": str(target)}])

    tx = await transaction_service.create_transaction(
        session, test_workspace.id, test_user.id,
        TransactionCreate(
            account_id=test_account.id, description="UBER *TRIP", amount=Decimal("20"),
            date=date.today(), type="debit",
        ),
    )

    tx = await _reload(session, tx.id)
    assert tx.category_id == target
    assert tx.category_source == "rule"
    events = await _events(session, tx.id)
    assert [e.source for e in events] == ["rule"]
    assert events[0].new_category_id == target


@pytest.mark.asyncio
async def test_rule_preview_writes_no_event(session, test_user, test_workspace, test_transactions, test_categories):
    target = test_categories[0].id
    await _make_rule(session, test_user, [{"op": "set_category", "value": str(target)}], value="NETFLIX")
    tx = test_transactions[4]  # NETFLIX, uncategorized

    preview = await rule_service.preview_rules_for_transaction(session, test_user.id, tx)

    assert preview.category_id == target
    assert preview.category_source == "rule"
    assert await _events(session, tx.id) == []
    assert (await _reload(session, tx.id)).category_id is None


@pytest.mark.asyncio
async def test_apply_single_rule_overwrite_skips_user(session, test_user, test_workspace, test_transactions, test_categories):
    target = test_categories[0].id
    rule = await _make_rule(session, test_user, [{"op": "set_category", "value": str(target)}])
    mine, theirs = test_transactions[0], test_transactions[2]  # UBER TRIP (Transporte), SALARIO
    mine.category_source = "user"
    theirs.description = "UBER SALARIO"
    theirs.category_source = "provider"
    await session.commit()

    await rule_service.apply_single_rule(
        session, test_workspace.id, rule, overwrite_existing_categories=True
    )

    mine = await _reload(session, mine.id)
    assert mine.category_id == test_categories[1].id
    assert mine.category_source == "user"
    assert await _events(session, mine.id) == []

    theirs = await _reload(session, theirs.id)
    assert theirs.category_id == target
    assert theirs.category_source == "rule"
    events = await _events(session, theirs.id)
    assert [e.source for e in events] == ["rule"]
    assert events[0].previous_source == "provider"


@pytest.mark.asyncio
async def test_apply_all_rules_preserves_user_category_and_notes(session, test_user, test_workspace, test_transactions, test_categories):
    target = test_categories[0].id
    await _make_rule(
        session, test_user,
        [{"op": "set_category", "value": str(target)}, {"op": "append_notes", "value": "#uber"}],
    )
    mine = test_transactions[0]  # UBER TRIP, Transporte
    mine.category_source = "user"
    mine.notes = "corrida do aeroporto"
    other = test_transactions[3]  # PIX RECEBIDO, uncategorized
    other.description = "UBER REEMBOLSO"
    await session.commit()

    await rule_service.apply_all_rules(session, test_workspace.id)

    mine = await _reload(session, mine.id)
    assert mine.category_id == test_categories[1].id
    assert mine.category_source == "user"
    assert mine.notes is not None and "corrida do aeroporto" in mine.notes
    assert await _events(session, mine.id) == []

    other = await _reload(session, other.id)
    assert other.category_id == target
    assert other.category_source == "rule"
    assert [e.source for e in await _events(session, other.id)] == ["rule"]


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


async def _sync_with(session, test_user, test_workspace, conn, accounts, transactions):
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=accounts)
    provider.get_transactions = AsyncMock(return_value=transactions)
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)


async def _make_connection(session, user) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user.id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}", institution_name="Banco",
        credentials={"token": "fake"}, status="active",
    )
    session.add(conn)
    await session.commit()
    return conn


async def _by_external_id(session, external_id) -> Transaction:
    result = await session.execute(
        select(Transaction)
        .where(Transaction.external_id == external_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_sync_pluggy_category_marks_provider(session, test_user, test_workspace, test_categories):
    conn = await _make_connection(session, test_user)
    account = AccountData(
        external_id="prov-acc", name="Checking", type="checking",
        balance=Decimal("100"), currency="BRL",
    )
    await _sync_with(session, test_user, test_workspace, conn, [account], [])
    await _sync_with(session, test_user, test_workspace, conn, [account], [
        TransactionData(
            external_id="prov-tx", description="RESTAURANTE", amount=Decimal("50"),
            date=date.today(), type="debit", currency="BRL", pluggy_category="Eating out",
        ),
    ])

    tx = await _by_external_id(session, "prov-tx")
    assert tx.category_id == test_categories[0].id  # Alimentação
    assert tx.category_source == "provider"
    assert [e.source for e in await _events(session, tx.id)] == ["provider"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("previous_source", "expected"), [("installment", "installment"), ("user", "user")])
async def test_sync_installment_inheritance_marks_installment(
    session, test_user, test_workspace, test_categories, previous_source, expected
):
    conn = await _make_connection(session, test_user)
    account_data = AccountData(
        external_id="inst-acc", name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    await _sync_with(session, test_user, test_workspace, conn, [account_data], [])
    account = (
        await session.execute(select(Account).where(Account.external_id == "inst-acc"))
    ).scalar_one()
    purchase = date(2026, 1, 15)
    inherited = test_categories[1].id
    session.add(Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        external_id="inst-2", description="LOJA 2/06", original_description="LOJA 2/06",
        amount=Decimal("100"), currency="BRL", date=date(2026, 2, 15),
        effective_date=date(2026, 2, 15), type="debit", source="sync", status="posted",
        category_id=inherited, category_source=previous_source,
        installment_number=2, total_installments=6,
        installment_total_amount=Decimal("600"), installment_purchase_date=purchase,
    ))
    await session.commit()

    await _sync_with(session, test_user, test_workspace, conn, [account_data], [
        TransactionData(
            external_id="inst-3", description="LOJA 3/06", amount=Decimal("100"),
            date=date(2026, 3, 15), type="debit", currency="BRL",
            pluggy_category="Eating out", installment_number=3, total_installments=6,
            installment_total_amount=Decimal("600"), installment_purchase_date=purchase,
        ),
    ])

    tx = await _by_external_id(session, "inst-3")
    assert tx.category_id == inherited
    assert tx.category_source == expected
    assert [e.source for e in await _events(session, tx.id)] == [expected]


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_user_choice_marks_user(session, test_user, test_workspace, test_account, test_categories):
    target = test_categories[0].id
    await import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [TransactionImport(
            description="MERCADO X", amount=Decimal("30"), date=date(2026, 1, 5),
            type="debit", category_id=target,
        )],
        "csv",
    )
    tx = (await session.execute(select(Transaction).where(Transaction.description == "MERCADO X"))).scalar_one()
    assert tx.category_id == target
    assert tx.category_source == "user"
    events = await _events(session, tx.id)
    assert [e.source for e in events] == ["user"]
    assert events[0].user_id == test_user.id


@pytest.mark.asyncio
async def test_import_csv_column_marks_import(session, test_user, test_workspace, test_account, test_categories):
    await import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [TransactionImport(
            description="FEIRA", amount=Decimal("30"), date=date(2026, 1, 6),
            type="debit", category_name="alimentação",
        )],
        "csv",
    )
    tx = (await session.execute(select(Transaction).where(Transaction.description == "FEIRA"))).scalar_one()
    assert tx.category_id == test_categories[0].id
    assert tx.category_source == "import"
    assert [e.source for e in await _events(session, tx.id)] == ["import"]


@pytest.mark.asyncio
async def test_import_rule_marks_rule(session, test_user, test_workspace, test_account, test_categories):
    target = test_categories[1].id
    await _make_rule(session, test_user, [{"op": "set_category", "value": str(target)}])
    await import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [TransactionImport(
            description="UBER VIAGEM", amount=Decimal("30"), date=date(2026, 1, 7),
            type="debit", category_name="Alimentação",
        )],
        "csv",
    )
    tx = (await session.execute(select(Transaction).where(Transaction.description == "UBER VIAGEM"))).scalar_one()
    assert tx.category_id == target
    assert tx.category_source == "rule"
    assert [e.source for e in await _events(session, tx.id)] == ["rule"]


# ---------------------------------------------------------------------------
# recurring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recurring_pending_marks_recurring(session, test_user, test_workspace, test_categories):
    account = await _make_manual_account(session, test_user)
    start = date.today() - timedelta(days=1)
    recurring = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=account.id, category_id=test_categories[0].id,
        description="ALUGUEL", amount=Decimal("1000"), currency="BRL", type="debit",
        frequency="monthly", start_date=start, next_occurrence=start, is_active=True,
    )
    session.add(recurring)
    await session.commit()

    created = await generate_pending(session, test_user.id, up_to=date.today(), include_manual=True)

    assert created == 1
    tx = (await session.execute(
        select(Transaction).where(Transaction.recurring_transaction_id == recurring.id)
    )).scalar_one()
    assert tx.category_id == test_categories[0].id
    assert tx.category_source == "recurring"
    assert [e.source for e in await _events(session, tx.id)] == ["recurring"]


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_category_transfer_records_events(session, test_user, test_workspace, test_transactions, test_categories):
    doomed = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Apagar", icon="tag", color="#000",
    )
    session.add(doomed)
    await session.flush()
    rule_tx, user_tx = test_transactions[3], test_transactions[4]
    rule_tx.category_id, rule_tx.category_source = doomed.id, "rule"
    user_tx.category_id, user_tx.category_source = doomed.id, "user"
    await session.commit()
    destination = test_categories[0].id

    assert await category_service.delete_category(
        session, doomed.id, test_workspace.id, transfer_to_id=destination
    )

    for tx, source in ((rule_tx, "rule"), (user_tx, "user")):
        row = await _reload(session, tx.id)
        assert row.category_id == destination
        assert row.category_source == source
        events = await _events(session, tx.id)
        assert len(events) == 1
        assert events[0].source == source
        assert events[0].previous_source == source
        assert events[0].new_category_id == destination
        # The deleted category's id is cleared by the FK (SET NULL) on
        # Postgres; SQLite keeps the dangling value. Either way it is not
        # the destination.
        assert events[0].previous_category_id != destination
    # Untouched rows get no event.
    assert await _events(session, test_transactions[0].id) == []


@pytest.mark.asyncio
async def test_write_paths_on_postgres(postgres_sessions):
    """Savepoints, the bulk event insert and ON DELETE SET NULL, for real."""
    from tests.test_categorization_models import _seed_pg

    async with postgres_sessions() as session:
        ws, doomed, txn = await _seed_pg(session)
        destination = Category(
            id=uuid.uuid4(), user_id=txn.user_id, workspace_id=ws, name="Mercado"
        )
        session.add(destination)
        await session.commit()

        await transaction_service.bulk_update_category(
            session, ws, [txn.id], doomed.id, user_id=txn.user_id
        )
        assert await category_service.delete_category(
            session, doomed.id, ws, transfer_to_id=destination.id
        )

        row = await _reload(session, txn.id)
        assert row.category_id == destination.id
        assert row.category_source == "user"
        events = await _events(session, txn.id)
        assert [e.source for e in events] == ["user", "user"]
        assert events[1].new_category_id == destination.id
        assert events[1].previous_category_id is None  # SET NULL on delete
