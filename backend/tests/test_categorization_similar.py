"""Similar rows for the Categorization section: no rule, no person, only the shown."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.categorization import CategorizationEvent, MerchantMemory
from app.models.transaction import Transaction

API = "/api/categorization"
SOURCES = ["none", "provider", "memory", "ai", "import", "recurring", "installment", "rule", "user", "legacy"]


def _tx(session, user, account, description, *, source="none", category_id=None, day=date(2026, 9, 10), workspace_id=None):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace_id or account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal("10.00"),
        currency="BRL",
        date=day,
        effective_date=day,
        type="debit",
        source="sync",
        status="posted",
        category_id=category_id,
        category_source=source,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    return tx


async def _similar(client, headers, ref, **extra):
    resp = await client.post(
        f"{API}/similar", headers=headers, json={"transaction_id": str(ref.id), **extra}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_similar_for_categorization_excludes_rule_and_person_rows(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, other = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "PG *IFOOD 000", source="user", category_id=food)
    rows = {
        s: _tx(session, test_user, test_account, f"IFOOD {i}", source=s, category_id=other)
        for i, s in enumerate(SOURCES)
    }
    await session.commit()

    body = await _similar(client, auth_headers, ref, exclude_rule=True)
    assert sorted(body["transaction_ids"]) == sorted(str(rows[s].id) for s in SOURCES[:7])

    body = await _similar(client, auth_headers, ref)
    assert str(rows["rule"].id) in body["transaction_ids"]
    assert len(body["transaction_ids"]) == 8


@pytest.mark.asyncio
async def test_similar_excludes_rows_already_at_the_category(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, other = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    applied = _tx(session, test_user, test_account, "IFOOD 2", source="user", category_id=food)
    open_ = _tx(session, test_user, test_account, "IFOOD 3", source="ai", category_id=other)
    await session.commit()

    body = await _similar(client, auth_headers, ref, exclude_rule=True)
    assert body["transaction_ids"] == [str(open_.id)]
    assert str(applied.id) not in body["transaction_ids"]


@pytest.mark.asyncio
async def test_similar_lists_the_rows_it_counts(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, other = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    _tx(session, test_user, test_account, "IFOOD 2", source="ai", category_id=other, day=date(2026, 9, 12))
    _tx(session, test_user, test_account, "IFOOD 3", source="none", day=date(2026, 9, 11))
    await session.commit()

    body = await _similar(client, auth_headers, ref, exclude_rule=True)
    assert [t["id"] for t in body["transactions"]] == body["transaction_ids"]
    assert body["count"] == len(body["transactions"]) == 2
    first = body["transactions"][0]
    assert first["description"] == "IFOOD 3"
    assert first["amount"] in ("10.00", 10.0, "10")
    assert (first["currency"], first["type"], first["category_source"]) == ("BRL", "debit", "none")
    assert first["category_id"] is None


@pytest.mark.asyncio
async def test_apply_similar_only_the_shown_rows(
    client, auth_headers, session, test_user, test_account, test_categories
):
    from app.models.workspace import Workspace

    food, other = test_categories[0].id, test_categories[1].id
    elsewhere = Workspace(id=uuid.uuid4(), name="Outro", created_by_user_id=test_user.id)
    session.add(elsewhere)
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    a = _tx(session, test_user, test_account, "IFOOD 2", source="ai", category_id=other)
    b = _tx(session, test_user, test_account, "IFOOD 3", source="none")
    c = _tx(session, test_user, test_account, "IFOOD 4", source="provider", category_id=other)
    # Shown, then filed by hand in another tab before the click.
    moved = _tx(session, test_user, test_account, "IFOOD 5", source="user", category_id=other)
    stranger = _tx(session, test_user, test_account, "UBER 9", source="none")
    # Same store, another workspace: an id the caller cannot reach.
    foreign = _tx(
        session, test_user, test_account, "IFOOD 6", source="none", workspace_id=elsewhere.id
    )
    await session.commit()
    shown = [a.id, b.id, moved.id, stranger.id, foreign.id]

    resp = await client.post(
        f"{API}/similar/apply",
        headers=auth_headers,
        json={
            "transaction_id": str(ref.id),
            "exclude_rule": True,
            "transaction_ids": [str(i) for i in shown],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == 2
    assert sorted(body["transaction_ids"]) == sorted([str(a.id), str(b.id)])
    # The shown ids left as they were, in the order they were given.
    assert body["skipped"] == [str(moved.id), str(stranger.id), str(foreign.id)]
    for tx in (a, b):
        fresh = await session.get(Transaction, tx.id, populate_existing=True)
        assert (fresh.category_id, fresh.category_source) == (food, "user")
    assert (await session.get(Transaction, c.id, populate_existing=True)).category_id == other
    assert (await session.get(Transaction, moved.id, populate_existing=True)).category_id == other
    assert (await session.get(Transaction, stranger.id, populate_existing=True)).category_source == "none"
    fresh_foreign = await session.get(Transaction, foreign.id, populate_existing=True)
    assert (fresh_foreign.category_id, fresh_foreign.category_source) == (None, "none")


@pytest.mark.asyncio
async def test_apply_similar_without_ids_leaves_rule_rows_alone(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, other = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    by_rule = _tx(session, test_user, test_account, "IFOOD 2", source="rule", category_id=other)
    open_ = _tx(session, test_user, test_account, "IFOOD 3", source="ai", category_id=other)
    await session.commit()

    resp = await client.post(
        f"{API}/similar/apply",
        headers=auth_headers,
        json={"transaction_id": str(ref.id), "exclude_rule": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["applied"], body["transaction_ids"], body["skipped"]) == (1, [str(open_.id)], [])
    fresh = await session.get(Transaction, by_rule.id, populate_existing=True)
    assert (fresh.category_id, fresh.category_source) == (other, "rule")
    assert (await session.get(Transaction, open_.id, populate_existing=True)).category_id == food


@pytest.mark.asyncio
async def test_apply_similar_marks_one_pick(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, other = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    for i in range(2, 5):
        _tx(session, test_user, test_account, f"IFOOD {i}", source="ai", category_id=other)
    await session.commit()

    resp = await client.post(
        f"{API}/similar/apply",
        headers=auth_headers,
        json={"transaction_id": str(ref.id), "exclude_rule": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == 3
    events = (
        await session.execute(
            select(CategorizationEvent).where(
                CategorizationEvent.id.in_([uuid.UUID(e) for e in body["event_ids"]])
            )
        )
    ).scalars().all()
    assert [e.memory_pick for e in events].count(True) == 1

    memory = (await session.execute(select(MerchantMemory))).scalars().one()
    await session.refresh(memory)
    assert memory.counts == {str(food): 1}

    undo = await client.post(f"{API}/undo", headers=auth_headers, json={"event_ids": body["event_ids"]})
    assert undo.json() == {"restored": 3, "skipped": 0}
    await session.refresh(memory)
    assert memory.counts == {}


@pytest.mark.asyncio
async def test_similar_for_categorization_counts_only_the_categorizable_base(
    client, auth_headers, session, test_user, test_account, test_categories
):
    """The section's N agrees with its own numbers: ignored rows are outside
    the base Revisar, Aplicadas and the scoreboard count (a purchase still
    pending at the bank is inside it). The other screens' toast keeps them."""
    food, other = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="user", category_id=food)
    open_ = _tx(session, test_user, test_account, "IFOOD 2", source="ai", category_id=other)
    pending = _tx(session, test_user, test_account, "IFOOD 3", source="none")
    pending.status = "pending"
    ignored = _tx(session, test_user, test_account, "IFOOD 4", source="ai", category_id=other)
    ignored.is_ignored = True
    await session.commit()

    body = await _similar(client, auth_headers, ref, exclude_rule=True)
    assert sorted(body["transaction_ids"]) == sorted(str(t.id) for t in (open_, pending))

    resp = await client.post(
        f"{API}/similar/apply",
        headers=auth_headers,
        json={
            "transaction_id": str(ref.id),
            "exclude_rule": True,
            "transaction_ids": [str(open_.id), str(pending.id), str(ignored.id)],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped"] == [str(ignored.id)]
    assert (await session.get(Transaction, pending.id, populate_existing=True)).category_id == food
    assert (await session.get(Transaction, ignored.id, populate_existing=True)).category_id == other

    body = await _similar(client, auth_headers, ref)
    assert body["transaction_ids"] == [str(ignored.id)]
