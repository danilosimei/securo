import uuid

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
async def test_learn_choice_clears_ai_cache(session, test_workspace, test_categories):
    ai, user = test_categories[0].id, test_categories[1].id
    await remember_ai_decision(session, test_workspace.id, "loja", ai, 95, "Loja")
    hit = (await lookup(session, test_workspace.id, ["loja"]))["loja"]
    assert (hit.ai_category_id, hit.ai_confidence, hit.ai_clean_name) == (ai, 95, "Loja")
    await learn_choice(session, test_workspace.id, "loja", user)
    hit = (await lookup(session, test_workspace.id, ["loja"]))["loja"]
    assert (hit.ai_category_id, hit.ai_confidence, hit.ai_clean_name) == (None, None, None)


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


@pytest.mark.asyncio
async def test_get_or_create_survives_lost_race(session, test_workspace, test_categories):
    from unittest.mock import patch

    a, b = test_categories[0].id, test_categories[1].id
    existing = MerchantMemory(
        id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="corrida",
        counts={str(a): 1},
    )
    session.add(existing)
    await session.flush()

    real_execute = session.execute
    calls = {"n": 0}

    class _Empty:
        def scalar_one_or_none(self):
            return None

    async def racy_execute(stmt, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first select "misses" the row another writer just made
            return _Empty()
        return await real_execute(stmt, *args, **kwargs)

    with patch.object(session, "execute", racy_execute):
        await learn_choice(session, test_workspace.id, "corrida", b)
    assert calls["n"] >= 2

    calls["n"] = 0
    with patch.object(session, "execute", racy_execute):
        await remember_ai_decision(session, test_workspace.id, "corrida", a, 91, "Corrida")

    rows = (await session.execute(select(MerchantMemory))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == existing.id
    assert rows[0].ai_category_id == a and rows[0].ai_confidence == 91


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
