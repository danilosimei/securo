import uuid

import pytest
from sqlalchemy import select

from app.models.transaction import Transaction
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
    tx = Transaction()
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
    # test_transactions[1] already has this category; with the same source
    # ("user") the change is a true no-op and writes no event.
    test_transactions[1].category_source = "user"
    await session.flush()
    ids = [t.id for t in test_transactions[:3]]
    n = await record_bulk(
        session, test_workspace.id, ids, source="user", new_category_id=target, user_id=None
    )
    assert n == 2
    assert await _events(session, test_transactions[1].id) == []
    assert len(await _events(session, test_transactions[0].id)) == 1


async def _run(session, workspace_id):
    run = CategorizationRun(workspace_id=workspace_id, kind="classify", trigger="manual")
    session.add(run)
    await session.flush()
    return run


@pytest.mark.asyncio
async def test_undo_by_run_restores_previous_category_and_source(session, test_workspace, test_transactions, test_categories):
    tx = test_transactions[3]  # no category
    assert tx.category_id is None
    run = await _run(session, test_workspace.id)
    ev = await record_change(session, tx, source="ai", new_category_id=test_categories[0].id, run_id=run.id)
    assert ev is not None
    result = await undo(session, test_workspace.id, run_id=run.id, user_id=None)
    assert (result.restored, result.skipped) == (1, 0)
    assert tx.category_id is None
    assert tx.category_source == "none"
    assert ev.undone_at is not None
    evs = await _events(session, tx.id)
    assert len(evs) == 2
    u = next(e for e in evs if e.source == "undo")
    assert u.previous_source == "ai"
    assert u.previous_category_id == test_categories[0].id
    assert u.new_category_id is None


@pytest.mark.asyncio
async def test_undo_chained_events_in_one_run_unwinds_all(session, test_workspace, test_transactions, test_categories):
    tx = test_transactions[3]
    run = await _run(session, test_workspace.id)
    await record_change(session, tx, source="provider", new_category_id=test_categories[0].id, run_id=run.id)
    await record_change(session, tx, source="ai", new_category_id=test_categories[1].id, run_id=run.id)
    result = await undo(session, test_workspace.id, run_id=run.id, user_id=None)
    assert (result.restored, result.skipped) == (2, 0)
    assert tx.category_id is None
    assert tx.category_source == "none"


@pytest.mark.asyncio
async def test_undo_restores_payee_only_when_event_changed_it(session, test_workspace, test_transactions, test_categories, test_user):
    from app.models.payee import Payee

    payee = Payee(user_id=test_user.id, workspace_id=test_workspace.id, name="Loja")
    session.add(payee)
    await session.flush()
    tx = test_transactions[3]
    with_payee = await record_change(session, tx, source="ai", new_category_id=test_categories[0].id, new_payee_id=payee.id)
    assert with_payee is not None and tx.payee_id == payee.id
    r = await undo(session, test_workspace.id, event_ids=[with_payee.id], user_id=None)
    assert r.restored == 1 and tx.payee_id is None

    other = test_transactions[4]
    other.payee_id = payee.id
    ev = await record_change(session, other, source="ai", new_category_id=test_categories[0].id)
    assert ev is not None
    await undo(session, test_workspace.id, event_ids=[ev.id], user_id=None)
    assert other.payee_id == payee.id  # event did not touch the payee


@pytest.mark.asyncio
async def test_undo_ignores_other_workspace_double_undo_and_undo_events(session, test_workspace, test_transactions, test_categories):
    tx = test_transactions[3]
    ev = await record_change(session, tx, source="ai", new_category_id=test_categories[0].id)
    assert ev is not None
    other = await undo(session, uuid.uuid4(), event_ids=[ev.id], user_id=None)
    assert (other.restored, other.skipped) == (0, 0)
    assert tx.category_id == test_categories[0].id

    assert (await undo(session, test_workspace.id, event_ids=[ev.id], user_id=None)).restored == 1
    again = await undo(session, test_workspace.id, event_ids=[ev.id], user_id=None)
    assert (again.restored, again.skipped) == (0, 1)

    undo_ev = next(e for e in await _events(session, tx.id) if e.source == "undo")
    refused = await undo(session, test_workspace.id, event_ids=[undo_ev.id], user_id=None)
    assert (refused.restored, refused.skipped) == (0, 1)


@pytest.mark.asyncio
async def test_invalid_source_is_rejected_without_touching_tx(session, test_transactions, test_categories):
    tx = test_transactions[3]
    assert await record_change(session, tx, source="bogus", new_category_id=test_categories[0].id) is None
    assert tx.category_id is None and tx.category_source == "none"


@pytest.mark.asyncio
async def test_event_write_failure_keeps_change_and_session_usable(session, test_transactions, test_categories, monkeypatch):
    real = events.CategorizationEvent

    def broken(**kw):
        kw["source"] = "bogus"  # violates the CHECK at flush time
        return real(**kw)

    monkeypatch.setattr(events, "CategorizationEvent", broken)
    tx = test_transactions[3]
    assert await record_change(session, tx, source="ai", new_category_id=test_categories[0].id) is None
    assert tx.category_id == test_categories[0].id and tx.category_source == "ai"
    monkeypatch.setattr(events, "CategorizationEvent", real)
    ev = await record_change(session, tx, source="user", new_category_id=test_categories[1].id)
    assert ev is not None
    await session.commit()


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
