"""Schema guarantees of the AI-categorization tables.

SQLite runs without foreign keys, so everything about ON DELETE is proven on
PostgreSQL; the rest (defaults, uniqueness) runs on the default SQLite session.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.categorization import (
    CategorizationEvent,
    CategorizationSettings,
    CategorizationSuggestion,
)
from app.models.category import Category
from app.models.transaction import Transaction

pytestmark = pytest.mark.asyncio


async def test_category_source_defaults_to_none(session, test_user, test_account):
    txn = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        description="Mercado", amount=Decimal("10"), date=date.today(),
        type="debit", source="manual", currency="BRL",
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    assert txn.category_id is None
    assert txn.category_source == "none"


async def test_settings_unique_per_workspace(session, test_user, test_account):
    ws = test_account.workspace_id
    session.add(CategorizationSettings(workspace_id=ws))
    await session.commit()
    session.add(CategorizationSettings(workspace_id=ws))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_settings_defaults(session, test_user, test_account):
    row = CategorizationSettings(workspace_id=test_account.workspace_id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    assert row.enabled is True
    assert row.auto_apply_threshold == 90
    assert row.monthly_cap_usd == Decimal("1.00")
    assert row.provider_category_map == {}


async def _seed_pg(session):
    from tests.test_admin_service import _make_user

    user = await _make_user(session, f"cat_{uuid.uuid4().hex[:8]}@test.com")
    acct = Account(
        id=uuid.uuid4(), user_id=user.id, name="Main",
        type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(acct)
    await session.flush()
    category = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=acct.workspace_id, name="Food")
    txn = Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=acct.id,
        description="Mercado", amount=Decimal("10"), date=date.today(),
        type="debit", source="manual", currency="BRL",
    )
    session.add_all([category, txn])
    await session.flush()
    return acct.workspace_id, category, txn


async def test_delete_category_with_events_and_suggestions_postgres(postgres_sessions):
    async with postgres_sessions() as session:
        ws, category, txn = await _seed_pg(session)
        event = CategorizationEvent(
            workspace_id=ws, transaction_id=txn.id, source="ai",
            new_category_id=category.id, previous_category_id=category.id,
        )
        suggestion = CategorizationSuggestion(
            workspace_id=ws, transaction_id=txn.id, merchant_key="mercado",
            category_id=category.id, alt_category_id=category.id, status="pending",
        )
        session.add_all([event, suggestion])
        await session.commit()

        await session.delete(category)
        await session.commit()

        for row in (event, suggestion):
            await session.refresh(row)
        assert event.new_category_id is None
        assert event.previous_category_id is None
        assert suggestion.category_id is None
        assert suggestion.alt_category_id is None


async def test_delete_transaction_cascades_events_postgres(postgres_sessions):
    async with postgres_sessions() as session:
        ws, _category, txn = await _seed_pg(session)
        session.add_all([
            CategorizationEvent(workspace_id=ws, transaction_id=txn.id, source="ai"),
            CategorizationSuggestion(
                workspace_id=ws, transaction_id=txn.id, merchant_key="mercado",
                status="pending",
            ),
        ])
        await session.commit()

        await session.delete(txn)
        await session.commit()

        assert (await session.execute(select(CategorizationEvent))).first() is None
        assert (await session.execute(select(CategorizationSuggestion))).first() is None
