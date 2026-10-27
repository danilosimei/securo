"""Histórico: every category change, one line per action, each undoable."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.categorization import CategorizationEvent, CategorizationRun
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.categorization import events

API = "/api/categorization"


def _tx(session, user, account, description, *, source="none", category_id=None, workspace_id=None):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace_id or account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal("10.00"),
        currency="BRL",
        date=date(2026, 9, 10),
        effective_date=date(2026, 9, 10),
        type="debit",
        source="sync",
        status="posted",
        category_id=category_id,
        category_source=source,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    return tx


async def _events_of(session, *txs):
    rows = await session.execute(
        select(CategorizationEvent)
        .where(CategorizationEvent.transaction_id.in_([t.id for t in txs]))
        .order_by(CategorizationEvent.created_at)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _history(client, headers, **params):
    resp = await client.get(f"{API}/history", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- action_id ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_names_its_action_with_the_header(
    client, auth_headers, session, test_user, test_account, test_categories
):
    """The screen picks the id, so its toast can undo and link to it."""
    tx = _tx(session, test_user, test_account, "IFOOD 1")
    await session.commit()
    action = uuid.uuid4()

    resp = await client.patch(
        f"/api/transactions/{tx.id}",
        headers={**auth_headers, "X-Action-Id": str(action)},
        json={"category_id": str(test_categories[0].id)},
    )
    assert resp.status_code == 200, resp.text
    [ev] = await _events_of(session, tx)
    assert ev.action_id == action


@pytest.mark.asyncio
async def test_one_request_is_one_action(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food = test_categories[0].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    rows = [_tx(session, test_user, test_account, f"IFOOD {i}") for i in (2, 3, 4)]
    await session.commit()

    resp = await client.post(
        f"{API}/similar/apply", headers=auth_headers, json={"transaction_id": str(ref.id)}
    )
    assert resp.status_code == 200, resp.text
    evs = await _events_of(session, *rows)
    assert len(evs) == 3
    assert len({ev.action_id for ev in evs}) == 1


@pytest.mark.asyncio
async def test_a_header_that_is_not_an_id_is_ignored(
    client, auth_headers, session, test_user, test_account, test_categories
):
    tx = _tx(session, test_user, test_account, "IFOOD 1")
    await session.commit()
    resp = await client.patch(
        f"/api/transactions/{tx.id}",
        headers={**auth_headers, "X-Action-Id": "not-an-id"},
        json={"category_id": str(test_categories[0].id)},
    )
    assert resp.status_code == 200, resp.text
    [ev] = await _events_of(session, tx)
    assert ev.action_id is not None


@pytest.mark.asyncio
async def test_a_run_is_one_action(session, test_user, test_account, test_categories, test_workspace):
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify",
        trigger="ingest", status="done", stats={}, detail={},
    )
    session.add(run)
    a = _tx(session, test_user, test_account, "UBER 1")
    b = _tx(session, test_user, test_account, "UBER 2")
    await session.flush()
    await events.record_change(session, a, source="ai", new_category_id=test_categories[1].id, run_id=run.id)
    await events.record_changes_bulk(
        session, test_workspace.id, [(b, test_categories[1].id, "ai")], run_id=run.id
    )
    await session.commit()
    assert {ev.action_id for ev in await _events_of(session, a, b)} == {run.id}


@pytest.mark.asyncio
async def test_outside_a_request_each_batch_is_one_action(
    session, test_user, test_account, test_categories, test_workspace
):
    a = _tx(session, test_user, test_account, "UBER 1")
    b = _tx(session, test_user, test_account, "UBER 2")
    c = _tx(session, test_user, test_account, "UBER 3")
    await session.flush()
    await events.record_changes_bulk(
        session, test_workspace.id, [(a, test_categories[1].id, "rule"), (b, test_categories[1].id, "rule")]
    )
    await events.record_change(session, c, source="rule", new_category_id=test_categories[1].id)
    await session.commit()
    ev_a, ev_b, ev_c = await _events_of(session, a, b, c)
    assert ev_a.action_id == ev_b.action_id
    assert ev_c.action_id != ev_a.action_id


# --- GET /history -------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_lists_one_line_per_action_newest_first(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0], test_categories[1]
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food.id)
    for i in (2, 3):
        _tx(session, test_user, test_account, f"IFOOD {i}", source="ai", category_id=transport.id)
    single = _tx(session, test_user, test_account, "PADARIA")
    await session.commit()

    await client.post(f"{API}/similar/apply", headers=auth_headers, json={"transaction_id": str(ref.id)})
    await client.patch(
        f"/api/transactions/{single.id}", headers=auth_headers, json={"category_id": str(food.id)}
    )

    body = await _history(client, auth_headers)
    assert body["total"] == 2
    latest, first = body["items"]
    assert latest["who"] == "you"
    assert latest["count"] == 1
    assert latest["from_categories"] == [None]
    assert latest["to_categories"] == [food.name]
    assert first["count"] == 2
    assert first["standing"] == 2
    assert first["from_categories"] == [transport.name]
    assert first["to_categories"] == [food.name]
    assert first["undone_at"] is None
    assert latest["at"] >= first["at"]


@pytest.mark.asyncio
async def test_history_filters_by_who(session, client, auth_headers, test_user, test_account, test_categories, test_workspace):
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify",
        trigger="ingest", status="done", stats={}, detail={},
    )
    session.add(run)
    a = _tx(session, test_user, test_account, "UBER 1")
    b = _tx(session, test_user, test_account, "MERCADO 1")
    await session.flush()
    await events.record_change(session, a, source="ai", new_category_id=test_categories[1].id, run_id=run.id)
    await events.record_change(session, b, source="rule", new_category_id=test_categories[0].id)
    await session.commit()

    assert [i["who"] for i in (await _history(client, auth_headers, who="ai"))["items"]] == ["ai"]
    assert [i["who"] for i in (await _history(client, auth_headers, who="auto"))["items"]] == ["rule"]
    assert (await _history(client, auth_headers, who="you"))["total"] == 0
    resp = await client.get(f"{API}/history", headers=auth_headers, params={"who": "bot"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_history_keeps_other_workspaces_out(
    client, auth_headers, session, test_user, test_account, test_categories
):
    elsewhere = Workspace(id=uuid.uuid4(), name="Outro", created_by_user_id=test_user.id)
    session.add(elsewhere)
    tx = _tx(session, test_user, test_account, "UBER 1", workspace_id=elsewhere.id)
    await session.flush()
    await events.record_change(session, tx, source="rule", new_category_id=test_categories[1].id)
    await session.commit()
    assert (await _history(client, auth_headers))["total"] == 0


# --- rows -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_rows_show_what_one_action_changed(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0], test_categories[1]
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food.id)
    row = _tx(session, test_user, test_account, "IFOOD 2", source="ai", category_id=transport.id)
    await session.commit()
    action = uuid.uuid4()
    await client.post(
        f"{API}/similar/apply",
        headers={**auth_headers, "X-Action-Id": str(action)},
        json={"transaction_id": str(ref.id)},
    )

    resp = await client.get(f"{API}/history/{action}/rows", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    [item] = body["items"]
    assert item["transaction_id"] == str(row.id)
    assert item["description"] == "IFOOD 2"
    assert (item["from_category"], item["to_category"]) == (transport.name, food.name)
    assert item["standing"] is True

    missing = await client.get(f"{API}/history/{uuid.uuid4()}/rows", headers=auth_headers)
    assert missing.status_code == 404


# --- undo by action -------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_an_action_restores_what_nobody_touched_since(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0], test_categories[1]
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food.id)
    kept = _tx(session, test_user, test_account, "IFOOD 2", source="ai", category_id=transport.id)
    touched = _tx(session, test_user, test_account, "IFOOD 3", source="ai", category_id=transport.id)
    await session.commit()
    action = uuid.uuid4()
    await client.post(
        f"{API}/similar/apply",
        headers={**auth_headers, "X-Action-Id": str(action)},
        json={"transaction_id": str(ref.id)},
    )
    # Changed again afterwards: this one stays.
    await client.patch(
        f"/api/transactions/{touched.id}", headers=auth_headers, json={"category_id": str(test_categories[2].id)}
    )

    line = next(i for i in (await _history(client, auth_headers))["items"] if i["action_id"] == str(action))
    assert (line["count"], line["standing"]) == (2, 1)

    resp = await client.post(f"{API}/undo", headers=auth_headers, json={"action_id": str(action)})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"restored": 1, "skipped": 1}
    fresh = await session.get(Transaction, kept.id, populate_existing=True)
    assert (fresh.category_id, fresh.category_source) == (transport.id, "ai")
    assert (await session.get(Transaction, touched.id, populate_existing=True)).category_id == test_categories[2].id

    items = (await _history(client, auth_headers))["items"]
    assert items[0]["who"] == "undo"
    assert items[0]["count"] == 1
    line = next(i for i in items if i["action_id"] == str(action))
    assert line["standing"] == 0


@pytest.mark.asyncio
async def test_undo_an_action_of_another_workspace_does_nothing(
    client, auth_headers, session, test_user, test_account, test_categories
):
    elsewhere = Workspace(id=uuid.uuid4(), name="Outro", created_by_user_id=test_user.id)
    session.add(elsewhere)
    tx = _tx(session, test_user, test_account, "UBER 1", workspace_id=elsewhere.id)
    await session.flush()
    ev = await events.record_change(session, tx, source="rule", new_category_id=test_categories[1].id)
    await session.commit()

    resp = await client.post(f"{API}/undo", headers=auth_headers, json={"action_id": str(ev.action_id)})
    assert resp.status_code == 200
    assert resp.json() == {"restored": 0, "skipped": 0}
    assert (await session.get(Transaction, tx.id, populate_existing=True)).category_id == test_categories[1].id


@pytest.mark.asyncio
async def test_history_marks_an_action_undone_when_all_of_it_went_back(
    client, auth_headers, session, test_user, test_account, test_categories
):
    tx = _tx(session, test_user, test_account, "PADARIA")
    await session.commit()
    action = uuid.uuid4()
    await client.patch(
        f"/api/transactions/{tx.id}",
        headers={**auth_headers, "X-Action-Id": str(action)},
        json={"category_id": str(test_categories[0].id)},
    )
    await client.post(f"{API}/undo", headers=auth_headers, json={"action_id": str(action)})

    line = next(i for i in (await _history(client, auth_headers))["items"] if i["action_id"] == str(action))
    assert line["undone_at"] is not None
    assert datetime.fromisoformat(line["undone_at"]) > datetime.now(timezone.utc) - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_a_run_line_counts_only_what_its_undo_reverts(
    client, auth_headers, session, test_user, test_account, test_categories, test_workspace
):
    """Undoing a run leaves its confirmations (same category) alone, so the
    line does not count them as undoable either."""
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify",
        trigger="ingest", status="done", stats={}, detail={},
    )
    session.add(run)
    moved = _tx(session, test_user, test_account, "UBER 1")
    confirmed = _tx(session, test_user, test_account, "UBER 2", source="provider", category_id=test_categories[1].id)
    await session.flush()
    await events.record_change(session, moved, source="ai", new_category_id=test_categories[1].id, run_id=run.id)
    await events.record_change(session, confirmed, source="ai", new_category_id=test_categories[1].id, run_id=run.id)
    await session.commit()

    [line] = (await _history(client, auth_headers))["items"]
    assert (line["who"], line["count"], line["standing"]) == ("ai", 2, 1)
    resp = await client.post(f"{API}/undo", headers=auth_headers, json={"action_id": str(run.id)})
    assert resp.json()["restored"] == 1
    assert (await session.get(Transaction, moved.id, populate_existing=True)).category_id is None
