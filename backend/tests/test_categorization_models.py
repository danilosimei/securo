"""Schema guarantees of the AI-categorization tables.

SQLite runs without foreign keys, so everything about ON DELETE is proven on
PostgreSQL; the rest (defaults, uniqueness) runs on the default SQLite session.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.categorization import (
    CATEGORY_SOURCES,
    CategorizationEvent,
    CategorizationRun,
    CategorizationSettings,
    CategorizationSuggestion,
    MerchantMemory,
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


def _txn(user, account, **extra):
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id,
        description="Mercado", amount=Decimal("10"), date=date.today(),
        type="debit", source="manual", currency="BRL", **extra,
    )


def _invalid_rows(ws, txn_id):
    run = {"workspace_id": ws, "kind": "classify", "trigger": "ingest", "status": "queued"}
    return {
        "event source": CategorizationEvent(workspace_id=ws, transaction_id=txn_id, source="bot"),
        "run kind": CategorizationRun(**{**run, "kind": "tidy"}),
        "run status": CategorizationRun(**{**run, "status": "paused"}),
        "run trigger": CategorizationRun(**{**run, "trigger": "cron"}),
        "suggestion status": CategorizationSuggestion(
            workspace_id=ws, transaction_id=txn_id, merchant_key="mercado", status="maybe",
        ),
        "threshold below": CategorizationSettings(workspace_id=ws, auto_apply_threshold=49),
        "threshold above": CategorizationSettings(workspace_id=ws, auto_apply_threshold=101),
    }


@pytest.mark.parametrize("case", [
    "event source", "run kind", "run status", "run trigger", "suggestion status",
    "threshold below", "threshold above",
])
async def test_check_constraints_reject_unknown_values(session, test_user, test_account, case):
    txn = _txn(test_user, test_account)
    session.add(txn)
    await session.commit()
    session.add(_invalid_rows(test_account.workspace_id, txn.id)[case])
    with pytest.raises(IntegrityError, match="CHECK"):
        await session.commit()
    await session.rollback()


async def test_transaction_category_source_is_checked(session, test_user, test_account):
    for source in CATEGORY_SOURCES:
        session.add(_txn(test_user, test_account, category_source=source))
    await session.commit()

    session.add(_txn(test_user, test_account, category_source="guess"))
    with pytest.raises(IntegrityError, match="CHECK"):
        await session.commit()
    await session.rollback()


async def test_merchant_memory_unique_per_workspace_and_key(session, test_user, test_account):
    ws = test_account.workspace_id
    session.add(MerchantMemory(workspace_id=ws, merchant_key="mercado", counts={}))
    await session.commit()
    session.add(MerchantMemory(workspace_id=ws, merchant_key="mercado", counts={}))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_json_columns_default_to_empty_object_in_the_database(
    session, test_user, test_account
):
    """Rows written without the ORM (raw SQL) get `{}` from the schema, as 097 has."""
    ws = test_account.workspace_id.hex
    run_id, settings_id, memory_id = (uuid.uuid4() for _ in range(3))
    await session.execute(
        text(
            "INSERT INTO categorization_runs (id, workspace_id, kind, trigger)"
            " VALUES (:id, :ws, 'classify', 'ingest')"
        ),
        {"id": run_id.hex, "ws": ws},
    )
    await session.execute(
        text("INSERT INTO categorization_settings (id, workspace_id) VALUES (:id, :ws)"),
        {"id": settings_id.hex, "ws": ws},
    )
    await session.execute(
        text(
            "INSERT INTO merchant_memory (id, workspace_id, merchant_key)"
            " VALUES (:id, :ws, 'mercado')"
        ),
        {"id": memory_id.hex, "ws": ws},
    )
    await session.commit()

    run = await session.get(CategorizationRun, run_id)
    settings = await session.get(CategorizationSettings, settings_id)
    memory = await session.get(MerchantMemory, memory_id)
    assert run is not None and settings is not None and memory is not None
    assert (run.stats, run.detail) == ({}, {})
    assert settings.provider_category_map == {}
    assert memory.counts == {}


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


async def test_migration_098_checks_the_model_sources():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions" / "098_transaction_category_source_check.py"
    )
    spec = importlib.util.spec_from_file_location("migration_098", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("098", "097")
    assert migration._SOURCES == CATEGORY_SOURCES


def _migration_098():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions" / "098_transaction_category_source_check.py"
    )
    spec = importlib.util.spec_from_file_location(f"migration_098_{uuid.uuid4().hex}", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_migration_098_adds_the_memory_pick_mark_like_the_model(monkeypatch):
    """The column the undo reads: not null, false by default, as the model;
    the choices written before it are marked right after it is added."""
    from types import SimpleNamespace

    migration = _migration_098()
    calls: list[tuple] = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(
        create_check_constraint=lambda *a, **k: calls.append(("check", a)),
        add_column=lambda table, column: calls.append(("add", table, column)),
        execute=lambda sql: calls.append(("sql", sql)),
        drop_column=lambda table, name: calls.append(("drop", table, name)),
        drop_constraint=lambda *a, **k: calls.append(("uncheck", a)),
    ))

    migration.upgrade()
    [(_, table, column)] = [c for c in calls if c[0] == "add"]
    assert (table, column.name, column.nullable) == ("categorization_events", "memory_pick", False)
    assert column.server_default is not None
    model = CategorizationEvent.__table__.c.memory_pick
    assert (model.nullable, model.server_default is not None) == (False, True)
    kinds = [c[0] for c in calls]
    assert kinds == ["check", "add", "sql", "sql"]
    assert [c[1] for c in calls if c[0] == "sql"] == [
        migration._MARK_ACCEPTS, migration._MARK_OTHER_PICKS,
    ]

    calls.clear()
    migration.downgrade()
    assert calls[0] == ("drop", "categorization_events", "memory_pick")


async def test_migration_098_marks_one_pick_per_accept_and_every_other_choice_postgres(postgres_sessions):
    """The backfill marks what the 097 code taught: one pick per store key
    per accept (its suggestions share one `resolved_at`), and every other
    standing person choice (edits, bulk, apply-similar). Clears, undone
    events and the pipeline's own stay unmarked."""
    from datetime import datetime, timedelta, timezone

    migration = _migration_098()
    async with postgres_sessions() as session:
        ws, category, seed = await _seed_pg(session)
        user_id, account_id = seed.user_id, seed.account_id

        def tx(description):
            row = Transaction(
                id=uuid.uuid4(), user_id=user_id, workspace_id=ws, account_id=account_id,
                description=description, amount=Decimal("10"), date=date.today(),
                type="debit", source="sync", currency="BRL",
            )
            session.add(row)
            return row

        def event(row, at, *, source="user", new=category.id, undone=None, person=True):
            ev = CategorizationEvent(
                id=uuid.uuid4(), workspace_id=ws, transaction_id=row.id, source=source,
                previous_category_id=None if new else category.id, new_category_id=new,
                user_id=user_id if person else None, created_at=at, undone_at=undone,
            )
            session.add(ev)
            return ev

        def suggestion(row, key, resolved, status="accepted"):
            session.add(CategorizationSuggestion(
                id=uuid.uuid4(), workspace_id=ws, transaction_id=row.id, merchant_key=key,
                status=status, resolved_at=resolved,
            ))

        first = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        again = first + timedelta(hours=1)
        a1, a2, b1, a3, c1, d1, e1 = (tx(n) for n in ("A1", "A2", "B1", "A3", "C1", "D1", "E1"))
        await session.flush()
        # One accept of two stores: K1 (two rows) and K2 (one row).
        for row in (a1, a2):
            suggestion(row, "K1", first)
        suggestion(b1, "K2", first)
        k1_older = event(a1, first - timedelta(seconds=2))
        k1_latest = event(a2, first - timedelta(seconds=1))
        k2 = event(b1, first - timedelta(seconds=1))
        # A later accept of K1 again: one more pick.
        suggestion(a3, "K1", again)
        k1_again = event(a3, again - timedelta(seconds=1))
        # A person edit after the accept: not the accept's, still a pick.
        edit = event(a1, again + timedelta(minutes=10))
        # An accept 097 undid: suggestion reopened, event undone.
        suggestion(c1, "K3", None, status="pending")
        event(c1, first, undone=first + timedelta(minutes=1))
        # A person's clear and a pipeline decision: never marked.
        event(d1, first, source="none", new=None)
        event(e1, first, source="ai", person=False)
        await session.commit()

        schema = session.get_bind().get_execution_options()["schema_translate_map"][None]
        await session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await session.execute(text(migration._MARK_ACCEPTS))
        await session.execute(text(migration._MARK_OTHER_PICKS))
        await session.commit()

        marked = set((await session.execute(
            select(CategorizationEvent.id).where(CategorizationEvent.memory_pick.is_(True))
        )).scalars().all())
        assert marked == {k1_latest.id, k2.id, k1_again.id, edit.id}
        assert k1_older.id not in marked
