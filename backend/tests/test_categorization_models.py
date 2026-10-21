"""Schema guarantees of the AI-categorization tables.

SQLite runs without foreign keys, so everything about ON DELETE is proven on
PostgreSQL; the rest (defaults, uniqueness) runs on the default SQLite session.
"""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
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


async def test_merchant_memory_note_defaults_to_null(session, test_user, test_account):
    row = MerchantMemory(workspace_id=test_account.workspace_id, merchant_key="mercado")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    assert row.note is None
    assert row.note_updated_at is None


async def test_run_kind_accepts_ask(session, test_account):
    ws = test_account.workspace_id
    run = {"workspace_id": ws, "trigger": "manual", "status": "done"}
    session.add(CategorizationRun(**run, kind="ask"))
    await session.commit()
    session.add(CategorizationRun(**run, kind="chat"))
    with pytest.raises(IntegrityError, match="CHECK"):
        await session.commit()
    await session.rollback()


async def test_migration_099_round_trip_postgres():
    """`alembic upgrade head` / `downgrade 098` / `upgrade head` on a scratch
    database: the columns come and go, the kind check follows, and the
    downgrade drops the `ask` runs the old check could not hold."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy.ext.asyncio import create_async_engine

    base = os.environ.get("POSTGRES_TEST_URL")
    if not base:
        if os.environ.get("CI"):
            pytest.fail("CI must supply POSTGRES_TEST_URL for PostgreSQL tests")
        pytest.skip("isolated PostgreSQL not configured")
    name = f"mig099_{uuid.uuid4().hex[:12]}"
    url = base.rsplit("/", 1)[0] + "/" + name
    admin = create_async_engine(base, isolation_level="AUTOCOMMIT")
    backend = Path(__file__).resolve().parent.parent

    def alembic(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=backend, check=True,
            env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True,
        )

    async def scalar(engine, sql):
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar_one()

    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    scratch = create_async_engine(url)
    try:
        await asyncio.to_thread(alembic, "upgrade", "head")
        cols = (
            "SELECT count(*) FROM information_schema.columns WHERE table_name='merchant_memory'"
            " AND column_name IN ('note', 'note_updated_at')"
        )
        assert await scalar(scratch, cols) == 2
        async with scratch.begin() as conn:
            await conn.execute(text("INSERT INTO workspaces (id, name) VALUES (gen_random_uuid(), 'w')"))
            await conn.execute(text(
                "INSERT INTO categorization_runs (id, workspace_id, kind, trigger, status)"
                " SELECT gen_random_uuid(), id, 'ask', 'manual', 'done' FROM workspaces"
            ))
        icon_default = (
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name='category_groups' AND column_name='icon'"
        )
        assert "circle-help" in await scalar(scratch, icon_default)
        await asyncio.to_thread(alembic, "downgrade", "098")
        assert await scalar(scratch, cols) == 0
        assert await scalar(scratch, "SELECT count(*) FROM categorization_runs") == 0
        assert "folder" in await scalar(scratch, icon_default)
        await asyncio.to_thread(alembic, "upgrade", "head")
        assert await scalar(scratch, cols) == 2
    finally:
        await scratch.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def _run_098_marks(session, migration):
    schema = session.get_bind().get_execution_options()["schema_translate_map"][None]
    await session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await session.execute(text(migration._MARK_ACCEPTS))
    await session.execute(text(migration._MARK_OTHER_PICKS))
    await session.commit()
    return set((await session.execute(
        select(CategorizationEvent.id).where(CategorizationEvent.memory_pick.is_(True))
    )).scalars().all())


async def test_migration_098_window_edge_and_accept_without_event_postgres(postgres_sessions):
    """An accept's event 2 minutes or more before `resolved_at` is outside
    the window: it falls to the "other choices" rule and is marked (the safe
    direction). An event exactly at `resolved_at` is the accept's carrier.
    An accepted (protected) row with no event marks nothing."""
    from datetime import datetime, timedelta, timezone

    migration = _migration_098()
    async with postgres_sessions() as session:
        ws, category, seed = await _seed_pg(session)
        user_id, account_id = seed.user_id, seed.account_id

        def tx(description, source="none"):
            row = Transaction(
                id=uuid.uuid4(), user_id=user_id, workspace_id=ws, account_id=account_id,
                description=description, amount=Decimal("10"), date=date.today(),
                type="debit", source="sync", currency="BRL",
                category_id=category.id if source != "none" else None, category_source=source,
            )
            session.add(row)
            return row

        def event(row, at):
            ev = CategorizationEvent(
                id=uuid.uuid4(), workspace_id=ws, transaction_id=row.id, source="user",
                previous_category_id=None, new_category_id=category.id,
                user_id=user_id, created_at=at,
            )
            session.add(ev)
            return ev

        def accepted(row, key, resolved):
            session.add(CategorizationSuggestion(
                id=uuid.uuid4(), workspace_id=ws, transaction_id=row.id, merchant_key=key,
                status="accepted", resolved_at=resolved,
            ))

        resolved = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        at_edge, past_edge, at_resolved, no_event = (
            tx(n, source="user") for n in ("EDGE", "PAST", "AT", "NOEV")
        )
        await session.flush()
        accepted(at_edge, "K_EDGE", resolved)
        accepted(past_edge, "K_PAST", resolved)
        accepted(at_resolved, "K_AT", resolved)
        accepted(no_event, "K_NOEV", resolved)
        edge = event(at_edge, resolved - timedelta(minutes=2))
        past = event(past_edge, resolved - timedelta(minutes=3))
        carrier = event(at_resolved, resolved)
        await session.commit()

        marked = await _run_098_marks(session, migration)

        assert marked == {edge.id, past.id, carrier.id}
        events_of_no_event_row = (await session.execute(
            select(func.count(CategorizationEvent.id))
            .where(CategorizationEvent.transaction_id == no_event.id)
        )).scalar_one()
        assert events_of_no_event_row == 0


async def test_migration_098_downgrade_round_trip_postgres():
    """`alembic upgrade 098` / `downgrade 097` / `upgrade 098` on a scratch
    database: the CHECK and the column come and go, and the second upgrade
    derives the marks again from the data as it stands."""
    import os
    import subprocess
    import sys
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    base = os.environ.get("POSTGRES_TEST_URL")
    if not base:
        if os.environ.get("CI"):
            pytest.fail("CI must supply POSTGRES_TEST_URL for PostgreSQL tests")
        pytest.skip("isolated PostgreSQL not configured")
    name = f"mig098_{uuid.uuid4().hex[:12]}"
    url = base.rsplit("/", 1)[0] + "/" + name
    admin = create_async_engine(base, isolation_level="AUTOCOMMIT")
    backend = Path(__file__).resolve().parent.parent

    def alembic(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=backend, check=True,
            env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True,
        )

    async def scalar(engine, sql):
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar_one()

    column = (
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_name='categorization_events' AND column_name='memory_pick'"
    )
    check = "SELECT count(*) FROM pg_constraint WHERE conname='ck_transaction_category_source'"
    marks = "SELECT count(*) FROM categorization_events WHERE memory_pick"

    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    scratch = create_async_engine(url)
    try:
        await asyncio.to_thread(alembic, "upgrade", "098")
        assert (await scalar(scratch, column), await scalar(scratch, check)) == (1, 1)
        async with async_sessionmaker(scratch, expire_on_commit=False)() as session:
            ws, category, seed = await _seed_pg(session)
            resolved = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
            session.add(CategorizationSuggestion(
                id=uuid.uuid4(), workspace_id=ws, transaction_id=seed.id, merchant_key="K",
                status="accepted", resolved_at=resolved,
            ))
            for seconds in (2, 1):
                session.add(CategorizationEvent(
                    id=uuid.uuid4(), workspace_id=ws, transaction_id=seed.id, source="user",
                    previous_category_id=None, new_category_id=category.id,
                    user_id=seed.user_id, created_at=resolved - timedelta(seconds=seconds),
                ))
            await session.commit()
        assert await scalar(scratch, marks) == 0

        await asyncio.to_thread(alembic, "downgrade", "097")
        assert (await scalar(scratch, column), await scalar(scratch, check)) == (0, 0)

        await asyncio.to_thread(alembic, "upgrade", "098")
        assert (await scalar(scratch, column), await scalar(scratch, check)) == (1, 1)
        # One accept of one store key: one pick, on its latest event.
        assert await scalar(scratch, marks) == 1
    finally:
        await scratch.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


def _migration_099():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions" / "099_merchant_note_and_ask_runs.py"
    )
    spec = importlib.util.spec_from_file_location(f"migration_099_{uuid.uuid4().hex}", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_group_and_collection_icons_default_to_an_icon_the_frontend_has(
    session, test_user, test_workspace
):
    """"folder" is not in `frontend/src/lib/category-icons.ts`; the
    "Outros" icon is."""
    from pathlib import Path

    from app.models.category_group import CategoryGroup
    from app.models.collection import Collection
    from app.schemas.category_group import CategoryGroupCreate
    from app.schemas.collection import CollectionCreate

    icons = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "category-icons.ts"
    )
    if icons.exists():
        assert "name: 'circle-help'" in icons.read_text(encoding="utf-8")
        assert "name: 'folder'" not in icons.read_text(encoding="utf-8")
    group = CategoryGroup(user_id=test_user.id, workspace_id=test_workspace.id, name="G")
    session.add(group)
    await session.commit()
    assert group.icon == "circle-help"
    assert CategoryGroupCreate(name="G").icon == "circle-help"
    assert Collection.__table__.c.icon.default.arg == "circle-help"
    assert CollectionCreate(name="C").icon == "circle-help"


async def test_migration_099_moves_folder_group_icons_to_circle_help(monkeypatch):
    from types import SimpleNamespace

    migration = _migration_099()
    calls: list[tuple] = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(
        add_column=lambda *a, **k: calls.append(("add", a)),
        drop_column=lambda *a, **k: calls.append(("drop", a)),
        create_check_constraint=lambda *a, **k: calls.append(("check", a)),
        drop_constraint=lambda *a, **k: calls.append(("uncheck", a)),
        alter_column=lambda *a, **k: calls.append(("alter", a, k)),
        execute=lambda sql: calls.append(("sql", sql)),
    ))

    migration.upgrade()

    assert ("sql", migration._FIX_GROUP_ICONS) in calls
    assert ("alter", ("category_groups", "icon"), {"server_default": "circle-help"}) in calls
    calls.clear()
    migration.downgrade()
    assert ("alter", ("category_groups", "icon"), {"server_default": "folder"}) in calls
    assert ("sql", migration._FIX_GROUP_ICONS) not in calls


async def test_migration_099_icon_fix_touches_only_folder_groups_postgres(postgres_sessions):
    from app.models.category_group import CategoryGroup

    migration = _migration_099()
    async with postgres_sessions() as session:
        ws, category, seed = await _seed_pg(session)
        folder, chosen = (
            CategoryGroup(user_id=seed.user_id, workspace_id=ws, name=name, icon=icon)
            for name, icon in (("Antigo", "folder"), ("Casa", "house"))
        )
        session.add_all([folder, chosen])
        await session.commit()

        schema = session.get_bind().get_execution_options()["schema_translate_map"][None]
        await session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await session.execute(text(migration._FIX_GROUP_ICONS))
        await session.commit()

        icons = dict((await session.execute(
            select(CategoryGroup.name, CategoryGroup.icon).where(CategoryGroup.workspace_id == ws)
        )).all())
        assert icons == {"Antigo": "circle-help", "Casa": "house"}
