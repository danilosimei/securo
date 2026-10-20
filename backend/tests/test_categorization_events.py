import pytest
from sqlalchemy import select

from app.models.categorization import CategorizationEvent, CategorizationRun
from app.services.categorization import events
from app.services.categorization.events import (
    PROTECTED_SOURCES,
    REVIEWABLE_SOURCES,
    is_protected,
    record_bulk,
    record_change,
    undo,
)


async def _events(session, tx_id):
    result = await session.execute(
        select(CategorizationEvent).where(CategorizationEvent.transaction_id == tx_id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_is_protected(test_transactions):
    tx = test_transactions[0]
    assert PROTECTED_SOURCES == {"user", "legacy"}
    assert REVIEWABLE_SOURCES == {"none", "provider", "memory", "ai"}
    for source, expected in [("user", True), ("legacy", True), ("ai", False), ("none", False), ("rule", False)]:
        tx.category_source = source
        assert is_protected(tx) is expected


@pytest.mark.asyncio
async def test_record_change_sets_source_and_previous_values(session, test_transactions, test_categories):
    tx = test_transactions[0]  # category Transporte, source none
    old = tx.category_id
    new = test_categories[0].id
    event = await record_change(
        session, tx, source="ai", new_category_id=new, confidence=95, reason="r", model="m"
    )
    assert event is not None
    assert tx.category_id == new
    assert tx.category_source == "ai"
    assert event.previous_category_id == old
    assert event.previous_source == "none"
    assert event.new_category_id == new
    assert event.confidence == 95 and event.reason == "r" and event.model == "m"
    assert event.workspace_id == tx.workspace_id


@pytest.mark.asyncio
async def test_record_change_noop_returns_none(session, test_transactions):
    tx = test_transactions[0]
    tx.category_source = "ai"
    event = await record_change(session, tx, source="ai", new_category_id=tx.category_id)
    assert event is None
    assert await _events(session, tx.id) == []


@pytest.mark.asyncio
async def test_record_bulk_writes_one_event_per_changed_row(session, test_workspace, test_transactions, test_categories):
    target = test_categories[0].id
    # test_transactions[1] already has this category and source "none"; source differs so it counts.
    # Make one a true no-op.
    test_transactions[1].category_source = "user"
    await session.flush()
    ids = [t.id for t in test_transactions[:3]]
    n = await record_bulk(
        session, test_workspace.id, ids, source="user", new_category_id=target, user_id=None
    )
    assert n == 2
    assert await _events(session, test_transactions[1].id) == []
    assert len(await _events(session, test_transactions[0].id)) == 1


@pytest.mark.asyncio
async def test_undo_by_run_restores_previous_category_and_source(session, test_workspace, test_transactions, test_categories):
    tx = test_transactions[3]  # no category
    assert tx.category_id is None
    ev = await record_change(session, tx, source="ai", new_category_id=test_categories[0].id)
    assert ev is not None
    run = CategorizationRun(workspace_id=test_workspace.id, kind="classify", trigger="manual")
    session.add(run)
    await session.flush()
    ev.run_id = run.id
    await session.flush()
    result = await undo(session, test_workspace.id, run_id=run.id, user_id=None)
    assert (result.restored, result.skipped) == (1, 0)
    assert tx.category_id is None
    assert tx.category_source == "none"
    assert ev.undone_at is not None
    assert len(await _events(session, tx.id)) == 2


@pytest.mark.asyncio
async def test_undo_skips_when_transaction_changed_after_event(session, test_workspace, test_transactions, test_categories):
    tx = test_transactions[3]
    ai = await record_change(session, tx, source="ai", new_category_id=test_categories[0].id)
    assert ai is not None
    await record_change(session, tx, source="user", new_category_id=test_categories[1].id)
    result = await undo(session, test_workspace.id, event_ids=[ai.id], user_id=None)
    assert (result.restored, result.skipped) == (0, 1)
    assert tx.category_id == test_categories[1].id
    assert tx.category_source == "user"


@pytest.mark.asyncio
async def test_user_choice_hook_called_for_user_source(session, test_transactions, test_categories):
    calls = []

    async def hook(s, workspace_id, tx, new_category_id):
        calls.append((workspace_id, tx.id, new_category_id))

    events.user_choice_hooks.append(hook)
    try:
        tx = test_transactions[3]
        await record_change(session, tx, source="ai", new_category_id=test_categories[0].id)
        assert calls == []
        await record_change(session, tx, source="user", new_category_id=test_categories[1].id)
        assert calls == [(tx.workspace_id, tx.id, test_categories[1].id)]
    finally:
        events.user_choice_hooks.remove(hook)
