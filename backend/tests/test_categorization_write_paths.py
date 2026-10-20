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
from app.services.categorization import events as categorization_events
from app.services.connection_service import sync_connection
from app.services.import_service import import_transactions
from app.services.recurring_transaction_service import generate_pending
from tests.test_recurring_match_sync import (
    _make_bill,
    _provider,
    _run_sync,
    _tx,
    conn_account,  # noqa: F401 (fixture)
)


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


# ---------------------------------------------------------------------------
# Fix round 1: preview parity, derived writes, merges, placeholders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_rule_agrees_with_apply_single_rule_on_protected_rows(
    session, test_user, test_workspace, test_transactions, test_categories
):
    target = test_categories[0].id
    actions = [{"op": "set_category", "value": str(target)}]
    conditions = [{"field": "description", "op": "contains", "value": "UBER"}]
    rule = await _make_rule(session, test_user, actions)
    mine = test_transactions[0]  # UBER TRIP, Transporte
    mine.category_source = "user"
    empty_mine = test_transactions[3]  # PIX RECEBIDO, uncategorized
    empty_mine.description = "UBER PIX"
    empty_mine.category_source = "user"
    other = test_transactions[2]  # SALARIO, provider
    other.description = "UBER SALARIO"
    other.category_source = "provider"
    await session.commit()

    preview = await rule_service.preview_rule(
        session, test_workspace.id, "and", conditions, actions,
        overwrite_existing_categories=True,
    )
    by_id = {item.id: item for item in preview.sample}
    assert by_id[mine.id].will_change is False
    assert by_id[mine.id].new_category_id == test_categories[1].id
    assert by_id[empty_mine.id].will_change is False
    assert by_id[empty_mine.id].new_category_id is None
    assert by_id[other.id].will_change is True
    assert preview.will_change == 1

    changed = await rule_service.apply_single_rule(
        session, test_workspace.id, rule, overwrite_existing_categories=True
    )
    assert changed == preview.will_change
    assert (await _reload(session, mine.id)).category_id == test_categories[1].id
    assert (await _reload(session, empty_mine.id)).category_id is None
    assert (await _reload(session, other.id)).category_id == target


@pytest.mark.asyncio
async def test_apply_rules_to_transaction_skips_user_row_with_empty_category(
    session, test_user, test_workspace, test_transactions, test_categories
):
    await _make_rule(
        session, test_user,
        [{"op": "set_category", "value": str(test_categories[0].id)}], value="NETFLIX",
    )
    tx = test_transactions[4]  # NETFLIX, uncategorized
    tx.category_source = "user"
    await session.commit()

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)

    assert tx.category_id is None
    assert tx.category_source == "user"
    assert await _events(session, tx.id) == []


@pytest.mark.asyncio
async def test_manual_edit_with_unchanged_category_keeps_origin(
    session, test_user, test_workspace, test_transactions
):
    tx = test_transactions[0]
    tx.category_source = "ai"
    await session.commit()

    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=tx.category_id, notes="nota nova"),
    )

    row = await _reload(session, tx.id)
    assert row.category_source == "ai"
    assert row.notes == "nota nova"
    assert await _events(session, tx.id) == []


@pytest.mark.asyncio
async def test_manual_edit_propagated_to_siblings_runs_user_hook_once(
    session, test_user, test_workspace, test_account, test_categories, monkeypatch
):
    calls: list[uuid.UUID] = []

    async def hook(_session, _workspace_id, tx, _new_category_id):
        calls.append(tx.id)

    monkeypatch.setattr(categorization_events, "user_choice_hooks", [hook])
    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="SOFA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=test_categories[0].id,
            ),
            installments=4,
        ),
    )
    assert calls == [created[0].id]  # the series is one choice
    calls.clear()

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=test_categories[1].id, apply_to="all"),
    )

    assert calls == [created[0].id]
    for tx in created:
        row = await _reload(session, tx.id)
        assert row.category_source == "user"
        assert (await _events(session, tx.id))[-1].source == "user"


@pytest.mark.asyncio
async def test_transfer_pair_unchanged_and_protected_is_left_alone(
    session, test_user, test_workspace, test_account, test_categories, monkeypatch
):
    calls: list[uuid.UUID] = []

    async def hook(_session, _workspace_id, tx, _new_category_id):
        calls.append(tx.id)

    monkeypatch.setattr(categorization_events, "user_choice_hooks", [hook])
    other = await _make_manual_account(session, test_user)
    debit, credit = await transaction_service.create_transfer(
        session, test_workspace.id, test_user.id,
        TransferCreate(
            from_account_id=test_account.id, to_account_id=other.id,
            description="XFER", amount=Decimal("100"), date=date.today(),
        ),
    )
    category = test_categories[0].id
    debit_row = await _reload(session, debit.id)
    credit_row = await _reload(session, credit.id)
    debit_row.category_id, debit_row.category_source = category, "ai"
    credit_row.category_id, credit_row.category_source = category, "user"
    await session.commit()

    # Unchanged leg and pair: nothing moves, nobody is promoted or stripped.
    await transaction_service.update_transaction(
        session, debit.id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=category, apply_to_transfer_pair=True),
    )
    assert (await _reload(session, credit.id)).category_source == "user"
    assert (await _reload(session, debit.id)).category_source == "ai"
    assert await _events(session, credit.id) == []

    # A real change cascades as the person's choice; hooks once (the leg).
    await transaction_service.update_transaction(
        session, debit.id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=test_categories[1].id, apply_to_transfer_pair=True),
    )
    paired = await _reload(session, credit.id)
    assert paired.category_id == test_categories[1].id
    assert paired.category_source == "user"
    assert calls == [debit.id]


@pytest.mark.asyncio
async def test_category_merge_events_carry_user_and_are_not_undone(
    session, test_user, test_workspace, test_transactions, test_categories
):
    doomed = Category(id=uuid.uuid4(), user_id=test_user.id, name="Apagar", icon="tag", color="#000")
    session.add(doomed)
    await session.flush()
    tx = test_transactions[4]
    tx.category_id, tx.category_source = doomed.id, "rule"
    await session.commit()

    assert await category_service.delete_category(
        session, doomed.id, test_workspace.id,
        transfer_to_id=test_categories[0].id, user_id=test_user.id,
    )
    (event,) = await _events(session, tx.id)
    assert event.reason == categorization_events.CATEGORY_MERGE_REASON
    assert event.user_id == test_user.id

    result = await categorization_events.undo(
        session, test_workspace.id, event_ids=[event.id], user_id=test_user.id
    )
    assert (result.restored, result.skipped) == (0, 1)
    row = await _reload(session, tx.id)
    assert row.category_id == test_categories[0].id
    assert row.category_source == "rule"


@pytest.mark.asyncio
async def test_oauth_callback_provider_category_marks_provider(
    session, test_user, test_workspace, test_categories
):
    from app.providers.base import ConnectionData
    from app.services.connection_service import handle_oauth_callback

    provider = AsyncMock()
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="oauth-prov", institution_name="Banco", credentials={"token": "abc"},
        accounts=[AccountData(
            external_id="oauth-acc", name="Checking", type="checking",
            balance=Decimal("0"), currency="BRL",
        )],
    ))
    provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="oauth-tx", description="RESTAURANTE", amount=Decimal("40"),
            date=date.today(), type="debit", currency="BRL", pluggy_category="Eating out",
        ),
    ])
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        await handle_oauth_callback(session, test_workspace.id, test_user.id, "code", "pluggy")

    tx = await _by_external_id(session, "oauth-tx")
    assert tx.category_id == test_categories[0].id
    assert tx.category_source == "provider"
    assert [e.source for e in await _events(session, tx.id)] == ["provider"]


async def _generated_placeholder(session, test_workspace, test_user, account, category_id):
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Netflix Subscription", amount=Decimal("39.90"),
        start_date=date(2025, 1, 10), category_id=category_id,
    )
    bill_id = bill.id
    await generate_pending(
        session, test_user.id, up_to=date(2025, 1, 10), include_manual=True,
        workspace_id=test_workspace.id,
    )
    placeholder = (await session.execute(
        select(Transaction).where(Transaction.recurring_transaction_id == bill_id)
    )).scalar_one()
    return placeholder.id


@pytest.mark.asyncio
@pytest.mark.parametrize("definition_has_category", [True, False])
async def test_sync_matched_placeholder_origin(
    session, test_user, test_workspace, conn_account, test_categories, definition_has_category  # noqa: F811
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    definition_category = test_categories[1].id if definition_has_category else None
    fallback_category = test_categories[0].id
    placeholder_id = await _generated_placeholder(
        session, test_workspace, test_user, account, definition_category
    )

    await _run_sync(session, conn_id, test_workspace, test_user, _provider([
        _tx(
            external_id="netflix-1", description="Netflix Subscription",
            amount=Decimal("39.90"), date=date(2025, 1, 11), pluggy_category="Eating out",
        ),
    ]), mock_rules=False)

    row = await _reload(session, placeholder_id)
    assert row.account_id == account_id
    assert row.external_id == "netflix-1"
    if definition_has_category:
        assert row.category_id == definition_category
        assert row.category_source == "recurring"
        assert [e.source for e in await _events(session, row.id)] == ["recurring"]
    else:
        assert row.category_id == fallback_category  # Pluggy "Eating out"
        assert row.category_source == "provider"
        assert [e.source for e in await _events(session, row.id)] == ["provider"]


@pytest.mark.asyncio
@pytest.mark.parametrize("definition_has_category", [True, False])
async def test_import_matched_placeholder_origin(
    session, test_user, test_workspace, conn_account, test_categories, definition_has_category  # noqa: F811
):
    _conn, account = conn_account
    account_id = account.id
    definition_category = test_categories[1].id if definition_has_category else None
    chosen = test_categories[2].id
    placeholder_id = await _generated_placeholder(
        session, test_workspace, test_user, account, definition_category
    )

    await import_transactions(
        session, test_workspace.id, test_user.id, account_id,
        [TransactionImport(
            description="Netflix Subscription", amount=Decimal("39.90"),
            date=date(2025, 1, 11), type="debit", category_id=chosen,
        )],
        "csv",
    )

    row = await _reload(session, placeholder_id)
    assert row.import_id is not None
    if definition_has_category:
        assert row.category_id == definition_category
        assert row.category_source == "recurring"
        assert [e.source for e in await _events(session, row.id)] == ["recurring"]
    else:
        assert row.category_id == chosen
        assert row.category_source == "user"
        events = await _events(session, row.id)
        assert [e.source for e in events] == ["user"]
        assert events[0].user_id == test_user.id


@pytest.mark.asyncio
async def test_apply_all_rules_writes_its_events_in_one_savepoint(
    session, test_user, test_workspace, test_transactions, test_categories, monkeypatch
):
    target = test_categories[0].id
    await _make_rule(session, test_user, [{"op": "set_category", "value": str(target)}], value="I")
    savepoints = 0
    real_begin_nested = session.begin_nested

    def counting_begin_nested():
        nonlocal savepoints
        savepoints += 1
        return real_begin_nested()

    monkeypatch.setattr(session, "begin_nested", counting_begin_nested)
    await rule_service.apply_all_rules(session, test_workspace.id)

    changed = [t for t in test_transactions if (await _events(session, t.id))]
    assert len(changed) >= 3  # UBER TRIP, IFOOD, SALARIO FEV, PIX RECEBIDO, NETFLIX contain "I"
    assert savepoints == 1


@pytest.mark.asyncio
async def test_record_changes_bulk_keeps_changes_when_event_insert_fails(
    session, test_workspace, test_transactions, test_categories, monkeypatch
):
    def broken_insert(_model):
        raise RuntimeError("boom")

    monkeypatch.setattr(categorization_events, "sa_insert", broken_insert)
    target = test_categories[2].id
    changed = await categorization_events.record_changes_bulk(
        session, test_workspace.id,
        [(t, target, "ai") for t in test_transactions[:2]],
    )

    assert changed == 2
    await session.commit()
    for t in test_transactions[:2]:
        row = await _reload(session, t.id)
        assert row.category_id == target
        assert row.category_source == "ai"
        assert await _events(session, t.id) == []


@pytest.mark.asyncio
async def test_manual_clear_marks_none_but_records_the_person(
    session, test_user, test_workspace, test_transactions
):
    """R27: a person who clears the category leaves the row reviewable again
    (`none`); the event still says who did it."""
    tx = test_transactions[1]  # IFOOD, categorized
    previous = tx.category_id
    assert previous is not None

    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=None)
    )

    tx = await _reload(session, tx.id)
    assert (tx.category_id, tx.category_source) == (None, "none")
    [event] = await _events(session, tx.id)
    assert (event.source, event.previous_category_id, event.new_category_id) == ("none", previous, None)
    assert event.user_id == test_user.id


@pytest.mark.asyncio
async def test_bulk_clear_marks_none_but_records_the_person(
    session, test_user, test_workspace, test_transactions
):
    rows = [t for t in test_transactions if t.category_id is not None][:2]
    assert len(rows) == 2

    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in rows], None, user_id=test_user.id
    )

    for row in rows:
        fresh = await _reload(session, row.id)
        assert (fresh.category_id, fresh.category_source) == (None, "none")
        [event] = await _events(session, row.id)
        assert (event.source, event.user_id) == ("none", test_user.id)


# ---------------------------------------------------------------------------
# Debt batch B1: one pick per store, siblings, import clears, real savepoint
# ---------------------------------------------------------------------------


def _manual_tx(session, user, account, description, category_id=None, source="none"):
    tx = Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, description=description,
        amount=Decimal("10"), date=date(2026, 9, 10), type="debit", source="manual",
        category_id=category_id, category_source=source,
    )
    session.add(tx)
    return tx


async def _memory_counts(session) -> dict[str, dict]:
    from app.models.categorization import MerchantMemory

    rows = (await session.execute(
        select(MerchantMemory).execution_options(populate_existing=True)
    )).scalars().all()
    return {row.merchant_key: dict(row.counts or {}) for row in rows}


@pytest.mark.asyncio
async def test_bulk_categorize_teaches_one_pick_per_store(
    session, test_user, test_workspace, test_account, test_categories
):
    """T7-3: fifty rows of one store selected together are one choice."""
    food, transport = test_categories[0].id, test_categories[1].id
    bread = [_manual_tx(session, test_user, test_account, "PADARIA SOL") for _ in range(3)]
    settled = _manual_tx(session, test_user, test_account, "PADARIA SOL", food, "ai")
    taxi = _manual_tx(session, test_user, test_account, "TAXI LUZ")
    # The person confirms a guess: it becomes theirs, so it is a pick (m1).
    guessed = _manual_tx(session, test_user, test_account, "CAFE LUA", food, "ai")
    await session.commit()

    matched = await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in [*bread, settled, taxi, guessed]], food,
        user_id=test_user.id,
    )

    assert matched == 6
    assert await _memory_counts(session) == {
        "PADARIA SOL": {str(food): 1}, "TAXI LUZ": {str(food): 1}, "CAFE LUA": {str(food): 1},
    }
    for tx in [*bread, settled, taxi, guessed]:
        row = await _reload(session, tx.id)
        assert (row.category_id, row.category_source) == (food, "user")

    # A store that already had the category in every row teaches nothing.
    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in bread], food, user_id=test_user.id,
    )
    assert (await _memory_counts(session))["PADARIA SOL"] == {str(food): 1}
    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in bread], transport, user_id=test_user.id,
    )
    assert (await _memory_counts(session))["PADARIA SOL"] == {str(food): 1, str(transport): 1}


@pytest.mark.asyncio
async def test_bulk_clear_takes_back_one_pick_per_store_and_the_ai_cache(
    session, test_user, test_workspace, test_account, test_categories
):
    """R27-U in bulk: once per store (and category it held), not per row."""
    from app.services.categorization import memory

    food = test_categories[0].id
    for _ in range(3):
        await memory.learn_choice(session, test_workspace.id, "PADARIA SOL", food)
    await memory.remember_ai_decision(session, test_workspace.id, "PADARIA SOL", food, 95, "Sol")
    bread = [_manual_tx(session, test_user, test_account, "PADARIA SOL", food, "user") for _ in range(4)]
    empty = _manual_tx(session, test_user, test_account, "PADARIA SOL")
    await session.commit()

    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in [*bread, empty]], None, user_id=test_user.id,
    )

    assert await _memory_counts(session) == {"PADARIA SOL": {str(food): 2}}
    hit = (await memory.lookup(session, test_workspace.id, ["PADARIA SOL"]))["PADARIA SOL"]
    assert (hit.ai_category_id, hit.ai_clean_name) == (None, "Sol")
    assert await _events(session, empty.id) == []


@pytest.mark.asyncio
async def test_failing_bulk_learner_keeps_the_rows_and_the_session(
    session, test_user, test_workspace, test_account, test_categories
):
    food = test_categories[0].id
    tx = _manual_tx(session, test_user, test_account, "PADARIA SOL")
    await session.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("learner failed")

    with patch("app.services.categorization.memory.learn_choice", boom):
        await transaction_service.bulk_update_category(
            session, test_workspace.id, [tx.id], food, user_id=test_user.id,
        )
    row = await _reload(session, tx.id)
    assert (row.category_id, row.category_source) == (food, "user")
    assert [e.source for e in await _events(session, tx.id)] == ["user"]


@pytest.mark.asyncio
async def test_series_edit_that_only_moves_siblings_still_teaches_the_choice(
    session, test_user, test_workspace, test_account, test_categories, monkeypatch
):
    """X-1: the anchor already had the category; its siblings follow it, and
    the memory learns the choice once, from the anchor."""
    food, transport = test_categories[0].id, test_categories[1].id
    calls: list[tuple[uuid.UUID, uuid.UUID | None]] = []

    async def hook(_session, _workspace_id, tx, new_category_id):
        calls.append((tx.id, new_category_id))

    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="SOFA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=food,
            ),
            installments=3,
        ),
    )
    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=transport),
    )
    monkeypatch.setattr(categorization_events, "user_choice_hooks", [hook])

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=transport, apply_to="all"),
    )

    assert calls == [(created[0].id, transport)]
    for tx in created:
        assert (await _reload(session, tx.id)).category_id == transport

    # Nothing moved: nothing to learn.
    calls.clear()
    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=transport, apply_to="all"),
    )
    assert calls == []


@pytest.mark.asyncio
async def test_import_leave_uncategorized_is_a_persons_clear(
    session, test_user, test_workspace, test_account, test_categories
):
    """F-3: "leave uncategorized" in the review table is the person's choice,
    so the row is only suggested a category later (R27)."""
    await _make_rule(
        session, test_user, [{"op": "set_category", "value": str(test_categories[0].id)}],
        value="LOJA VAZIA",
    )
    await import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [TransactionImport(
            description="LOJA VAZIA", amount=Decimal("30"), date=date(2026, 1, 5),
            type="debit", force_uncategorized=True,
        )],
        "csv",
    )
    tx = (await session.execute(
        select(Transaction).where(Transaction.description == "LOJA VAZIA")
    )).scalar_one()
    assert (tx.category_id, tx.category_source) == (None, "none")
    [event] = await _events(session, tx.id)
    assert (event.source, event.user_id) == ("none", test_user.id)
    assert (event.previous_category_id, event.new_category_id) == (None, None)
    assert await categorization_events.cleared_by_person(session, [tx.id]) == {tx.id}


async def test_record_changes_bulk_survives_a_real_failing_event_insert_postgres(postgres_sessions):
    """T3-3: a run that does not exist trips the events' FK inside the
    savepoint; the rows keep their change and the session stays usable."""
    from tests.test_categorization_models import _seed_pg

    async with postgres_sessions() as session:
        ws, category, txn = await _seed_pg(session)
        await session.commit()

        changed = await categorization_events.record_changes_bulk(
            session, ws, [(txn, category.id, "ai")], run_id=uuid.uuid4(),
        )
        assert changed == 1
        await session.commit()

        row = await _reload(session, txn.id)
        assert (row.category_id, row.category_source) == (category.id, "ai")
        assert await _events(session, txn.id) == []


# ---------------------------------------------------------------------------
# B1 review minors (m1, m2, m4, m6, m7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_edit_runs_every_registered_hook_once_per_store(
    session, test_user, test_workspace, test_account, test_categories, monkeypatch
):
    """m6: bulk edits go through the hooks, so a hook added later runs too:
    once per store for a pick, once per store and category for a clear."""
    food, transport = test_categories[0].id, test_categories[1].id
    picks: list[str] = []
    clears: list[tuple[str, uuid.UUID]] = []

    async def choice(_s, _ws, tx, new_category_id):
        assert new_category_id == food
        picks.append(tx.description)

    async def clear(_s, _ws, tx, previous_category_id):
        clears.append((tx.description, previous_category_id))

    monkeypatch.setattr(categorization_events, "user_choice_hooks", [choice])
    monkeypatch.setattr(categorization_events, "user_clear_hooks", [clear])
    bread = [_manual_tx(session, test_user, test_account, "PADARIA SOL") for _ in range(3)]
    taxi = _manual_tx(session, test_user, test_account, "TAXI LUZ")
    await session.commit()

    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in [*bread, taxi]], food, user_id=test_user.id,
    )
    assert sorted(picks) == ["PADARIA SOL", "TAXI LUZ"]

    bread[0].category_id = transport
    await session.commit()
    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in bread], None, user_id=test_user.id,
    )
    assert sorted(clears, key=str) == sorted(
        [("PADARIA SOL", food), ("PADARIA SOL", transport)], key=str
    )


@pytest.mark.asyncio
async def test_bulk_clear_of_a_store_with_two_categories_takes_one_pick_from_each(
    session, test_user, test_workspace, test_account, test_categories
):
    from app.services.categorization import memory

    food, transport = test_categories[0].id, test_categories[1].id
    for category in (food, food, transport, transport):
        await memory.learn_choice(session, test_workspace.id, "PADARIA SOL", category)
    rows = [
        _manual_tx(session, test_user, test_account, "PADARIA SOL", category, "user")
        for category in (food, food, transport)
    ]
    await session.commit()

    await transaction_service.bulk_update_category(
        session, test_workspace.id, [t.id for t in rows], None, user_id=test_user.id,
    )

    assert await _memory_counts(session) == {"PADARIA SOL": {str(food): 1, str(transport): 1}}


@pytest.mark.asyncio
async def test_failing_bulk_unlearner_keeps_the_clear_and_the_session(
    session, test_user, test_workspace, test_account, test_categories
):
    food = test_categories[0].id
    tx = _manual_tx(session, test_user, test_account, "PADARIA SOL", food, "user")
    await session.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("unlearner failed")

    with patch("app.services.categorization.memory.unlearn_choice", boom):
        await transaction_service.bulk_update_category(
            session, test_workspace.id, [tx.id], None, user_id=test_user.id,
        )
    row = await _reload(session, tx.id)
    assert (row.category_id, row.category_source) == (None, "none")
    [event] = await _events(session, tx.id)
    assert (event.source, event.user_id) == ("none", test_user.id)


@pytest.mark.asyncio
async def test_failing_store_grouping_keeps_the_bulk_edit(
    session, test_user, test_workspace, test_account, test_categories
):
    food = test_categories[0].id
    tx = _manual_tx(session, test_user, test_account, "PADARIA SOL", None, "none")
    await session.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("grouping failed")

    with patch("app.services.categorization.memory.one_per_store", boom):
        await transaction_service.bulk_update_category(
            session, test_workspace.id, [tx.id], food, user_id=test_user.id,
        )
    row = await _reload(session, tx.id)
    assert (row.category_id, row.category_source) == (food, "user")


@pytest.mark.asyncio
async def test_series_clear_that_only_empties_siblings_takes_the_pick_back_once(
    session, test_user, test_workspace, test_account, test_categories
):
    """m2, the clear mirror of X-1: the anchor is already empty and only its
    siblings lose the category; the memory loses one pick, not one per row."""
    from app.services.categorization import memory

    food = test_categories[0].id
    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="SOFA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=food,
            ),
            installments=4,
        ),
    )
    [key] = await _memory_counts(session)  # the series taught one pick
    for _ in range(3):
        await memory.learn_choice(session, test_workspace.id, key, food)
    await session.commit()

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id, TransactionUpdate(category_id=None),
    )
    assert await _memory_counts(session) == {key: {str(food): 3}}

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=None, apply_to="all"),
    )

    assert await _memory_counts(session) == {key: {str(food): 2}}
    for tx in created:
        assert (await _reload(session, tx.id)).category_id is None

    # Nothing moved: nothing to take back.
    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=None, apply_to="all"),
    )
    assert await _memory_counts(session) == {key: {str(food): 2}}


@pytest.mark.asyncio
async def test_import_leave_uncategorized_on_a_matched_placeholder_is_a_persons_clear(
    session, test_user, test_workspace, conn_account  # noqa: F811
):
    """m4: the F-3 rule on the path that upgrades a recurring placeholder."""
    _conn, account = conn_account
    placeholder_id = await _generated_placeholder(session, test_workspace, test_user, account, None)

    await import_transactions(
        session, test_workspace.id, test_user.id, account.id,
        [TransactionImport(
            description="Netflix Subscription", amount=Decimal("39.90"),
            date=date(2025, 1, 11), type="debit", force_uncategorized=True,
        )],
        "csv",
    )

    row = await _reload(session, placeholder_id)
    assert row.import_id is not None
    assert (row.category_id, row.category_source) == (None, "none")
    [event] = await _events(session, row.id)
    assert (event.source, event.user_id, event.new_category_id) == ("none", test_user.id, None)
    assert await categorization_events.cleared_by_person(session, [row.id]) == {row.id}
