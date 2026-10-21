import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import select

from app.models.categorization import MerchantMemory
from app.models.payee import Payee
from app.schemas.transaction import TransactionUpdate
from app.services import transaction_service
from app.services.categorization import events
from app.services.categorization.events import record_change
from app.services.categorization.memory import (
    MemoryHit,
    forget_ai_decisions,
    is_consistent,
    key_for_transaction,
    learn_choice,
    lookup,
    remember_ai_decision,
)
from app.services.categorization.merchant_key import merchant_key


def test_consistency_threshold():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert is_consistent({str(a): 4, str(b): 1}) == (a, True)
    assert is_consistent({str(a): 1}) == (a, False)
    assert is_consistent({str(a): 3, str(b): 2}) == (a, False)
    assert is_consistent({}) == (None, False)


@pytest.mark.asyncio
async def test_hook_registered_once():
    from app.services.categorization import memory

    assert events.user_choice_hooks.count(memory.on_user_choice) == 1


@pytest.mark.asyncio
async def test_lookup_returns_hit_for_every_key(session, test_workspace, test_categories):
    cat = test_categories[0].id
    await learn_choice(session, test_workspace.id, "padaria", cat)
    await learn_choice(session, test_workspace.id, "padaria", cat)
    hits = await lookup(session, test_workspace.id, ["padaria", "missing"])
    assert set(hits) == {"padaria", "missing"}
    assert hits["padaria"] == MemoryHit(
        category_id=cat, consistent=True, distribution={str(cat): 2},
        ai_category_id=None, ai_confidence=None, ai_clean_name=None,
    )
    assert hits["missing"].category_id is None
    assert hits["missing"].consistent is False
    assert hits["missing"].distribution == {}


@pytest.mark.asyncio
async def test_learn_choice_twice_in_session_single_row(session, test_workspace, test_categories):
    a, b = test_categories[0].id, test_categories[1].id
    await learn_choice(session, test_workspace.id, "loja", a)
    await learn_choice(session, test_workspace.id, "loja", b)
    await learn_choice(session, test_workspace.id, "loja", a)
    rows = (await session.execute(select(MerchantMemory))).scalars().all()
    assert len(rows) == 1
    assert rows[0].counts == {str(a): 2, str(b): 1}
    assert rows[0].last_user_category_id == a


@pytest.mark.asyncio
async def test_user_edit_feeds_memory(
    session, test_user, test_workspace, test_transactions, test_categories
):
    tx = test_transactions[4]  # NETFLIX
    target = test_categories[0].id
    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=target)
    )
    key = await key_for_transaction(session, tx)
    hits = await lookup(session, test_workspace.id, [key])
    assert hits[key].distribution == {str(target): 1}


@pytest.mark.asyncio
async def test_ai_events_do_not_feed_memory(session, test_workspace, test_transactions, test_categories):
    tx = test_transactions[4]
    await record_change(session, tx, source="ai", new_category_id=test_categories[0].id)
    await record_change(session, tx, source="memory", new_category_id=test_categories[1].id)
    await record_change(session, tx, source="user", new_category_id=test_categories[2].id, derived=True)
    assert (await session.execute(select(MerchantMemory))).scalars().all() == []


@pytest.mark.asyncio
async def test_learn_choice_clears_ai_cache_but_keeps_the_store_name(
    session, test_workspace, test_categories
):
    """The clean name describes the store, not a category decision (T17-1b)."""
    ai, user = test_categories[0].id, test_categories[1].id
    await remember_ai_decision(session, test_workspace.id, "loja", ai, 95, "Loja")
    hit = (await lookup(session, test_workspace.id, ["loja"]))["loja"]
    assert (hit.ai_category_id, hit.ai_confidence, hit.ai_clean_name) == (ai, 95, "Loja")
    await learn_choice(session, test_workspace.id, "loja", user)
    hit = (await lookup(session, test_workspace.id, ["loja"]))["loja"]
    assert (hit.ai_category_id, hit.ai_confidence, hit.ai_clean_name) == (None, None, "Loja")


@pytest.mark.asyncio
async def test_forget_ai_decisions(session, test_workspace, test_categories):
    c = test_categories[0].id
    for k in ("a", "b"):
        await remember_ai_decision(session, test_workspace.id, k, c, 90, k)
    await forget_ai_decisions(session, test_workspace.id, ["a"])
    hits = await lookup(session, test_workspace.id, ["a", "b"])
    assert hits["a"].ai_category_id is None and hits["b"].ai_category_id == c
    await forget_ai_decisions(session, test_workspace.id)
    hits = await lookup(session, test_workspace.id, ["b"])
    assert hits["b"].ai_category_id is None
    assert (hits["b"].ai_confidence, hits["b"].ai_clean_name) == (None, "b")


@pytest.mark.asyncio
async def test_key_for_transaction_without_payee(session, test_transactions):
    tx = test_transactions[0]
    assert await key_for_transaction(session, tx) == merchant_key(tx.description)


@pytest.mark.asyncio
async def test_key_for_transaction_with_payees(
    session, test_user, test_workspace, test_transactions
):
    tx = test_transactions[1]
    sync = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                 name="Padaria Estrela", source="sync")
    doc = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                name="Padaria Sol", source="document")
    session.add_all([sync, doc])
    await session.flush()

    tx.payee_id = sync.id
    assert await key_for_transaction(session, tx) == merchant_key(
        tx.description, payee_name="Padaria Estrela", payee_source="sync"
    )
    tx.payee_id = doc.id
    assert await key_for_transaction(session, tx) == merchant_key(
        tx.description, payee_name="Padaria Sol", payee_source="document"
    )


@pytest.mark.asyncio
async def test_lookup_empty_keys(session, test_workspace):
    assert await lookup(session, test_workspace.id, []) == {}


async def test_get_or_create_survives_lost_race_postgres(postgres_sessions):
    """Another writer creates the store's row right after this session looked
    for it: the real unique-key IntegrityError, absorbed by the savepoint."""
    from sqlalchemy import event
    from sqlalchemy.util import await_only

    from tests.test_categorization_models import _seed_pg

    async with postgres_sessions() as setup:
        ws, category, _txn = await _seed_pg(setup)
        await setup.commit()
        a = category.id

    async def other_writer() -> uuid.UUID:
        async with postgres_sessions() as other:
            row = MerchantMemory(
                id=uuid.uuid4(), workspace_id=ws, merchant_key="corrida", counts={str(a): 1},
            )
            other.add(row)
            await other.commit()
            return row.id

    async with postgres_sessions() as session:
        connection = await session.connection()
        raced: list[uuid.UUID] = []

        def race(_conn, _cursor, statement, *_args):
            text = statement.lstrip().upper()
            if not raced and text.startswith("SELECT") and "MERCHANT_MEMORY" in text:
                raced.append(await_only(other_writer()))

        event.listen(connection.sync_connection, "after_cursor_execute", race)
        try:
            await learn_choice(session, ws, "corrida", a)
        finally:
            event.remove(connection.sync_connection, "after_cursor_execute", race)
        await session.commit()

        rows = (await session.execute(select(MerchantMemory))).scalars().all()
        assert [r.id for r in rows] == raced
        assert rows[0].counts == {str(a): 2}


@pytest.mark.asyncio
async def test_failing_learner_keeps_user_change_and_session(
    session, test_user, test_workspace, test_transactions, test_categories
):
    from unittest.mock import patch

    tx = test_transactions[4]
    target = test_categories[0].id

    async def boom(*args, **kwargs):
        raise RuntimeError("learner failed")

    with patch("app.services.categorization.memory.learn_choice", boom):
        await transaction_service.update_transaction(
            session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=target)
        )
    await session.refresh(tx)
    assert tx.category_id == target and tx.category_source == "user"
    await learn_choice(session, test_workspace.id, "ok", target)  # session still usable
    assert (await lookup(session, test_workspace.id, ["ok"]))["ok"].distribution == {str(target): 1}


# ---------------------------------------------------------------------------
# Debt batch B1: corrupt counts, set-based moves, chunked forgets, clears
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_counts_entries_are_dropped(session, test_workspace, test_categories):
    """One bad JSON entry must not fail the whole run (T5-1)."""
    from app.services.categorization.memory import move_category

    a, b = test_categories[0].id, test_categories[1].id
    session.add(MerchantMemory(
        id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="loja",
        counts={str(a): 2, "not-a-uuid": 3, str(b): "x", str(uuid.uuid4()): None},
    ))
    await session.flush()

    hit = (await lookup(session, test_workspace.id, ["loja"]))["loja"]
    assert (hit.category_id, hit.consistent, hit.distribution) == (a, True, {str(a): 2})

    await learn_choice(session, test_workspace.id, "loja", a)
    await move_category(session, test_workspace.id, a, b)
    row = (await session.execute(select(MerchantMemory))).scalar_one()
    assert row.counts == {str(b): 3}


@pytest.mark.asyncio
async def test_move_category_loads_only_the_rows_that_mention_it(
    session, test_workspace, test_categories
):
    """Filtered in SQL, not by loading every row of the workspace (T6-4)."""
    from sqlalchemy import event as sqlalchemy_event

    from app.services.categorization.memory import move_category

    gone, other, target = (c.id for c in test_categories)
    ws = test_workspace.id
    rows = {
        "counts": MerchantMemory(merchant_key="counts", counts={str(gone): 2}),
        "last": MerchantMemory(merchant_key="last", counts={}, last_user_category_id=gone),
        "ai": MerchantMemory(merchant_key="ai", counts={}, ai_category_id=gone, ai_confidence=95),
        "unrelated": MerchantMemory(merchant_key="unrelated", counts={str(other): 4}),
    }
    for row in rows.values():
        row.id = uuid.uuid4()
        row.workspace_id = ws
        session.add(row)
    await session.commit()
    session.expunge_all()
    loaded: set[str] = set()

    def record(_session, instance):
        if isinstance(instance, MerchantMemory):
            loaded.add(instance.merchant_key)

    sqlalchemy_event.listen(session.sync_session, "loaded_as_persistent", record)
    try:
        await move_category(session, ws, gone, target)
    finally:
        sqlalchemy_event.remove(session.sync_session, "loaded_as_persistent", record)

    assert loaded == {"counts", "last", "ai"}
    fresh = {
        r.merchant_key: r for r in (await session.execute(
            select(MerchantMemory).execution_options(populate_existing=True)
        )).scalars().all()
    }
    assert fresh["counts"].counts == {str(target): 2}
    assert fresh["last"].last_user_category_id == target
    assert fresh["ai"].ai_category_id == target
    assert fresh["unrelated"].counts == {str(other): 4}


async def test_move_category_filters_in_sql_on_postgres(postgres_sessions):
    """The JSONB `counts` cast to text matches on PostgreSQL too."""
    from app.services.categorization.memory import move_category
    from tests.test_categorization_models import _seed_pg

    async with postgres_sessions() as session:
        ws, gone, _txn = await _seed_pg(session)
        kept = str(uuid.uuid4())
        session.add_all([
            MerchantMemory(id=uuid.uuid4(), workspace_id=ws, merchant_key="loja",
                           counts={str(gone.id): 2}),
            MerchantMemory(id=uuid.uuid4(), workspace_id=ws, merchant_key="outra",
                           counts={kept: 1}),
        ])
        await session.commit()

        await move_category(session, ws, gone.id, None)
        await session.commit()

        rows = {
            r.merchant_key: r.counts for r in (await session.execute(
                select(MerchantMemory).execution_options(populate_existing=True)
            )).scalars().all()
        }
        assert rows == {"loja": {}, "outra": {kept: 1}}


@pytest.mark.asyncio
async def test_move_category_deleting_keeps_the_store_name(session, test_workspace, test_categories):
    from app.services.categorization.memory import move_category

    gone = test_categories[0].id
    await remember_ai_decision(session, test_workspace.id, "loja", gone, 95, "Loja")
    await move_category(session, test_workspace.id, gone, None)
    hit = (await lookup(session, test_workspace.id, ["loja"]))["loja"]
    assert (hit.ai_category_id, hit.ai_confidence, hit.ai_clean_name) == (None, None, "Loja")


def _statements(connection, prefix: str) -> tuple[list[str], Callable[[], None]]:
    """Record the statements starting with `prefix` run on `connection`."""
    from sqlalchemy import event

    seen: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(prefix):
            seen.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", record)
    return seen, lambda: event.remove(connection.sync_connection, "before_cursor_execute", record)


@pytest.mark.asyncio
async def test_forget_ai_decisions_chunks_its_keys(session, test_workspace, test_categories):
    """asyncpg caps a statement at 32767 parameters (T10-3b)."""
    c = test_categories[0].id
    keys = [f"k{i}" for i in range(2500)]
    for key in ("k0", "k1500", "k2499", "outside"):
        await remember_ai_decision(session, test_workspace.id, key, c, 90, key)

    updates, stop = _statements(await session.connection(), "UPDATE MERCHANT_MEMORY")
    try:
        await forget_ai_decisions(session, test_workspace.id, keys)
    finally:
        stop()

    assert len(updates) == 3
    hits = await lookup(session, test_workspace.id, ["k0", "k1500", "k2499", "outside"])
    assert [hits[k].ai_category_id for k in ("k0", "k1500", "k2499")] == [None, None, None]
    assert hits["outside"].ai_category_id == c


@pytest.mark.asyncio
async def test_keys_for_matches_key_for_transaction_with_one_payee_query(
    session, test_user, test_workspace, test_transactions
):
    from app.services.categorization.memory import keys_for

    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                  name="Padaria Estrela", source="sync")
    session.add(payee)
    await session.flush()
    test_transactions[0].payee_id = payee.id
    test_transactions[1].payee_id = payee.id
    test_transactions[2].description = "LOJA " + "X" * 300
    await session.flush()
    expected = {tx.id: (await key_for_transaction(session, tx))[:255] for tx in test_transactions}

    selects, stop = _statements(await session.connection(), "SELECT")
    try:
        keys = await keys_for(session, test_transactions)
    finally:
        stop()

    assert keys == expected
    assert len(selects) == 1


@pytest.mark.asyncio
async def test_clear_hook_registered_once():
    from app.services.categorization import memory

    assert events.user_clear_hooks.count(memory.on_user_clear) == 1


@pytest.mark.asyncio
async def test_person_clear_takes_back_one_pick_and_the_ai_cache(
    session, test_user, test_workspace, test_transactions, test_categories
):
    """R27-U: a person who removes a store's category takes that choice back
    out of the memory (one pick) and drops the store's cached AI decision."""
    tx = test_transactions[4]  # NETFLIX, no category
    food, transport = test_categories[0].id, test_categories[1].id
    key = await key_for_transaction(session, tx)
    await learn_choice(session, test_workspace.id, key, food)
    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=food)
    )
    await remember_ai_decision(session, test_workspace.id, key, transport, 95, "Netflix")
    assert (await lookup(session, test_workspace.id, [key]))[key].distribution == {str(food): 2}

    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=None)
    )

    hit = (await lookup(session, test_workspace.id, [key]))[key]
    assert hit.distribution == {str(food): 1}
    assert (hit.ai_category_id, hit.ai_confidence, hit.ai_clean_name) == (None, None, "Netflix")


@pytest.mark.asyncio
async def test_unlearn_choice_says_whether_it_took_a_pick(session, test_workspace, test_categories):
    from app.services.categorization import memory

    food, transport = test_categories[0].id, test_categories[1].id
    assert await memory.unlearn_choice(session, test_workspace.id, "NUNCA VISTA", food) is False
    await memory.learn_choice(session, test_workspace.id, "PADARIA", food)
    assert await memory.unlearn_choice(session, test_workspace.id, "PADARIA", transport) is False
    assert await memory.unlearn_choice(session, test_workspace.id, "PADARIA", food) is True
    # At zero: the floor, nothing left to take.
    assert await memory.unlearn_choice(session, test_workspace.id, "PADARIA", food) is False


@pytest.mark.asyncio
async def test_clearing_a_store_never_taught_leaves_the_memory_alone(
    session, test_user, test_workspace, test_transactions
):
    tx = test_transactions[1]  # IFOOD, categorized by the fixture, never taught
    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=None)
    )
    assert (await session.execute(select(MerchantMemory))).scalars().all() == []


@pytest.mark.asyncio
async def test_failing_unlearner_keeps_the_clear_and_session(
    session, test_user, test_workspace, test_transactions, test_categories
):
    from unittest.mock import patch

    tx = test_transactions[1]

    async def boom(*args, **kwargs):
        raise RuntimeError("unlearner failed")

    with patch("app.services.categorization.memory.unlearn_choice", boom):
        await transaction_service.update_transaction(
            session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=None)
        )
    await session.refresh(tx)
    assert (tx.category_id, tx.category_source) == (None, "none")
    await learn_choice(session, test_workspace.id, "ok", test_categories[0].id)  # still usable


# ---------------------------------------------------------------------------
# B1 review minors (m3, m5, m8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlearn_choice_survives_corrupt_counts(session, test_workspace, test_categories):
    """m5: the same tolerance as `lookup` and `learn_choice`."""
    from app.services.categorization.memory import unlearn_choice

    a = test_categories[0].id
    good = MerchantMemory(id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="loja",
                          counts={str(a): 2, "not-a-uuid": "x"})
    broken = MerchantMemory(id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="outra",
                            counts=[1, 2])
    session.add_all([good, broken])
    await session.flush()

    await unlearn_choice(session, test_workspace.id, "loja", a)
    await unlearn_choice(session, test_workspace.id, "outra", a)

    assert (good.counts, broken.counts) == ({str(a): 1}, {})


@pytest.mark.asyncio
async def test_lookup_matches_a_store_key_longer_than_the_column(
    session, test_user, test_workspace, test_account, test_categories
):
    """m8: a store key over 255 characters is learned under its stored form,
    and the lookup by the full key finds it."""
    from datetime import date
    from decimal import Decimal

    from app.models.transaction import Transaction
    from app.services.categorization.memory import stored_key

    food = test_categories[0].id
    description = "LOJA " + "MUITO COMPRIDA " * 30
    key = merchant_key(description)
    assert len(key) > 255
    for _ in range(2):
        tx = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
            description=description, amount=Decimal("10"), date=date(2026, 9, 10),
            type="debit", source="manual",
        )
        session.add(tx)
        await session.flush()
        await record_change(session, tx, source="user", new_category_id=food, user_id=test_user.id)

    [row] = (await session.execute(select(MerchantMemory))).scalars().all()
    assert row.merchant_key == stored_key(key)
    hit = (await lookup(session, test_workspace.id, [key]))[key]
    assert (hit.category_id, hit.consistent) == (food, True)


@pytest.mark.asyncio
async def test_clear_takes_the_pick_back_from_the_store_the_row_was_in(
    session, test_user, test_workspace, test_transactions, test_categories
):
    """m3: clearing the category and renaming the row in one save unlearns
    the store that held the pick, not the new name."""
    tx = test_transactions[4]  # NETFLIX
    food = test_categories[0].id
    key = await key_for_transaction(session, tx)
    await learn_choice(session, test_workspace.id, key, food)
    await learn_choice(session, test_workspace.id, key, food)
    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=food)
    )
    assert (await lookup(session, test_workspace.id, [key]))[key].distribution == {str(food): 3}

    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=None, description="SPOTIFY"),
    )

    rows = {r.merchant_key: r.counts for r in (await session.execute(
        select(MerchantMemory).execution_options(populate_existing=True)
    )).scalars().all()}
    assert rows == {key: {str(food): 2}}


# --- store note ---------------------------------------------------------------


async def _memory_row(session, ws, key):
    return (
        await session.execute(
            select(MerchantMemory).where(
                MerchantMemory.workspace_id == ws, MerchantMemory.merchant_key == key
            )
        )
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_set_note_creates_row_without_picks(session, test_workspace):
    from app.services.categorization.memory import set_note

    row = await set_note(session, test_workspace.id, "AURINO", "jardineiro")
    assert row is not None
    assert row.note == "jardineiro"
    assert row.note_updated_at is not None
    assert row.counts == {}
    assert is_consistent(row.counts) == (None, False)


@pytest.mark.asyncio
async def test_set_note_scrubs_documents_and_truncates(session, test_workspace):
    from app.services.categorization.memory import NOTE_LENGTH, set_note

    row = await set_note(
        session, test_workspace.id, "AURINO",
        "Aurino jardineiro CPF 123.456.789-09 conta 12345-6 " + "x" * 300,
    )
    assert row is not None
    assert row.note is not None
    assert "123.456.789-09" not in row.note
    assert "12345-6" not in row.note
    assert len(row.note) <= NOTE_LENGTH == 200


@pytest.mark.asyncio
async def test_saved_note_and_prompt_note_are_cleaned_by_one_function(
    session, test_workspace, monkeypatch
):
    """The note saved and the note sent to the model are the same text: one
    cleaning, shared by the memory and the prompts."""
    from app.services.categorization import ask_prompts, memory, merchant_key, prompts

    assert prompts.clean_note is merchant_key.clean_note
    assert ask_prompts.clean_note is merchant_key.clean_note
    assert memory.NOTE_LENGTH == merchant_key.NOTE_LENGTH == 200
    monkeypatch.setattr(memory, "clean_note", lambda text: "limpa")

    row = await memory.set_note(session, test_workspace.id, "AURINO", "qualquer")

    assert row is not None and row.note == "limpa"


@pytest.mark.asyncio
async def test_set_note_forgets_ai_decision(session, test_workspace, test_categories):
    from app.services.categorization.memory import set_note

    await remember_ai_decision(session, test_workspace.id, "AURINO", test_categories[0].id, 90, "Aurino")
    row = await set_note(session, test_workspace.id, "AURINO", "jardineiro")
    assert row is not None
    assert row.ai_category_id is None
    assert row.ai_confidence is None
    assert row.ai_clean_name == "Aurino"


@pytest.mark.asyncio
async def test_set_note_unchanged_keeps_ai_decision(session, test_workspace, test_categories):
    from app.services.categorization.memory import set_note

    await set_note(session, test_workspace.id, "AURINO", "jardineiro")
    stamp = (await _memory_row(session, test_workspace.id, "AURINO")).note_updated_at
    await remember_ai_decision(session, test_workspace.id, "AURINO", test_categories[0].id, 90, None)
    row = await set_note(session, test_workspace.id, "AURINO", "jardineiro")
    assert row is not None
    assert row.ai_category_id == test_categories[0].id
    assert row.ai_confidence == 90
    assert row.note_updated_at == stamp


@pytest.mark.asyncio
async def test_clearing_note_keeps_counts(session, test_workspace, test_categories):
    from app.services.categorization.memory import set_note

    cat = test_categories[0].id
    await learn_choice(session, test_workspace.id, "AURINO", cat)
    await set_note(session, test_workspace.id, "AURINO", "jardineiro")
    row = await set_note(session, test_workspace.id, "AURINO", "   ")
    assert row is not None
    assert row.note is None
    assert row.counts == {str(cat): 1}


@pytest.mark.asyncio
async def test_lookup_returns_note(session, test_workspace):
    from app.services.categorization.memory import set_note

    await set_note(session, test_workspace.id, "AURINO", "jardineiro")
    hits = await lookup(session, test_workspace.id, ["AURINO", "OUTRA"])
    assert hits["AURINO"].note == "jardineiro"
    assert hits["OUTRA"].note is None


@pytest.mark.asyncio
async def test_clearing_note_without_memory_creates_no_row(session, test_workspace):
    from app.services.categorization.memory import set_note

    assert await set_note(session, test_workspace.id, "AURINO", None) is None
    assert await set_note(session, test_workspace.id, "AURINO", "   ") is None
    assert await _memory_row(session, test_workspace.id, "AURINO") is None


@pytest.mark.asyncio
async def test_nameless_rows_never_teach_nor_take_back_picks(
    session, test_user, test_workspace, test_account, test_categories
):
    """A row with no description and no payee has the key `UNKNOWN`, shared
    by every such row: a choice on one is not a store's choice."""
    from datetime import date
    from decimal import Decimal

    from app.models.categorization import CategorizationEvent
    from app.models.transaction import Transaction
    from app.services.categorization import memory
    from app.services.categorization.merchant_key import UNKNOWN_KEY

    food = test_categories[0].id
    tx = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description="", amount=Decimal("10"), currency="BRL",
        date=date(2026, 9, 10), type="debit", source="sync", status="posted",
    )
    session.add(tx)
    await session.commit()
    assert await key_for_transaction(session, tx) == UNKNOWN_KEY

    assert await memory.on_user_choice(session, test_workspace.id, tx, food) is False
    await learn_choice(session, test_workspace.id, UNKNOWN_KEY, food)
    assert await memory.on_user_clear(session, test_workspace.id, tx, food) is False
    assert (await lookup(session, test_workspace.id, [UNKNOWN_KEY]))[UNKNOWN_KEY].distribution == {
        str(food): 1
    }

    await transaction_service.update_transaction(
        session, tx.id, test_workspace.id, test_user.id, TransactionUpdate(category_id=food)
    )
    events_ = (await session.execute(select(CategorizationEvent))).scalars().all()
    assert [e.memory_pick for e in events_] == [False]
    assert (await lookup(session, test_workspace.id, [UNKNOWN_KEY]))[UNKNOWN_KEY].distribution == {
        str(food): 1
    }
