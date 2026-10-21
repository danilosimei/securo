"""Aplicadas beyond the pipeline's changes (confirmations, the person's rows,
other origins) and the section's scoreboard."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.categorization import CategorizationEvent, CategorizationSuggestion
from app.models.category import Category
from app.models.transaction import Transaction

API = "/api/categorization"


def _tx(
    session, user, account, description, *,
    source="none", category_id=None, created_at=None, **extra,
):
    day = date(2026, 9, 10)
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal("10.00"),
        currency="BRL",
        date=day,
        effective_date=day,
        type="debit",
        source=extra.pop("tx_source", "sync"),
        status=extra.pop("status", "posted"),
        category_id=category_id,
        category_source=source,
        created_at=created_at or datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    return tx


def _event(session, tx, *, source="ai", previous=None, new=None, confidence=95,
           created_at=None, reason="mercado"):
    ev = CategorizationEvent(
        id=uuid.uuid4(),
        workspace_id=tx.workspace_id,
        transaction_id=tx.id,
        source=source,
        previous_source="none",
        previous_category_id=previous,
        new_category_id=new,
        confidence=confidence,
        reason=reason,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(ev)
    return ev


def _suggest(session, tx, key, *, category_id=None):
    sug = CategorizationSuggestion(
        id=uuid.uuid4(),
        workspace_id=tx.workspace_id,
        transaction_id=tx.id,
        merchant_key=key,
        category_id=category_id,
        confidence=60,
        reason="parece",
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    session.add(sug)
    return sug


async def _applied(client, headers, source):
    resp = await client.get(f"{API}/applied?source={source}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Confirmadas ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_applied_confirmed_lists_only_confirmations(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food, transport = test_categories[0].id, test_categories[1].id
    confirmed = _tx(session, test_user, test_account, "PADARIA", source="ai", category_id=food)
    by_memory = _tx(session, test_user, test_account, "MERCADO", source="memory", category_id=food)
    changed = _tx(session, test_user, test_account, "UBER", source="ai", category_id=transport)
    undone = _tx(session, test_user, test_account, "FEIRA", source="ai", category_id=food)
    by_rule = _tx(session, test_user, test_account, "POSTO", source="rule", category_id=transport)
    await session.flush()
    now = datetime.now(timezone.utc)
    kept = _event(session, confirmed, source="ai", previous=food, new=food, created_at=now)
    _event(session, by_memory, source="memory", previous=food, new=food, confidence=None,
           created_at=now - timedelta(minutes=1))
    _event(session, changed, source="ai", previous=food, new=transport)
    gone = _event(session, undone, source="ai", previous=food, new=food)
    gone.undone_at = now
    _event(session, by_rule, source="rule", previous=transport, new=transport)
    await session.commit()

    body = await _applied(client, auth_headers, "confirmed")
    assert body["total"] == 2
    assert [i["transaction"]["id"] for i in body["items"]] == [str(confirmed.id), str(by_memory.id)]
    first = body["items"][0]
    assert first["event_id"] == str(kept.id)
    assert first["previous_category_id"] == first["new_category_id"] == str(food)
    # The default view still leaves confirmations out.
    default = (await client.get(f"{API}/applied", headers=auth_headers)).json()
    assert [i["transaction"]["id"] for i in default["items"]] == [str(changed.id)]


# --- Suas / Outras origens --------------------------------------------------------


@pytest.mark.asyncio
async def test_applied_user_lists_rows_by_origin(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food, transport = test_categories[0].id, test_categories[1].id
    now = datetime.now(timezone.utc)
    mine = _tx(session, test_user, test_account, "PADARIA ESTRELA", source="user",
               category_id=food, created_at=now)
    old = _tx(session, test_user, test_account, "MERCADO SOL", source="legacy",
              category_id=transport, created_at=now - timedelta(days=30))
    # Not the person's, or not categorizable, or no category: left out.
    _tx(session, test_user, test_account, "UBER", source="ai", category_id=transport)
    _tx(session, test_user, test_account, "CLEARED", source="user", category_id=None)
    _tx(session, test_user, test_account, "ESTORNO", source="user", category_id=food, is_ignored=True)
    _tx(session, test_user, test_account, "AGUARDANDO", source="user", category_id=food, status="pending")
    _tx(session, test_user, test_account, "SALDO", source="user", category_id=food, tx_source="opening_balance")
    _tx(session, test_user, test_account, "PARA CONTA", source="user", category_id=food,
        transfer_pair_id=uuid.uuid4())
    await session.flush()
    standing = _event(session, mine, source="user", previous=None, new=food, confidence=None,
                      reason=None, created_at=now - timedelta(minutes=5))
    # Newer than the standing one, but undone: it does not describe the row.
    undone = _event(session, mine, source="user", previous=food, new=transport,
                    created_at=now - timedelta(minutes=3))
    undone.undone_at = now - timedelta(minutes=2)
    await session.commit()

    body = await _applied(client, auth_headers, "user")
    assert body["total"] == 2
    items = body["items"]
    assert [i["transaction"]["id"] for i in items] == [str(mine.id), str(old.id)]
    first, second = items
    assert first["event_id"] == str(standing.id)
    assert first["source"] == "user"
    assert first["new_category_id"] == str(food)
    assert first["store_name"] == "Padaria Estrela"
    assert second["event_id"] is None
    assert second["run_id"] is None
    assert second["source"] == "legacy"
    assert second["new_category_id"] == str(transport)
    assert second["previous_category_id"] is None
    assert second["confidence"] is None and second["reason"] is None
    assert second["merchant_key"]

    paged = await client.get(f"{API}/applied?source=user&limit=1&offset=1", headers=auth_headers)
    assert [i["transaction"]["id"] for i in paged.json()["items"]] == [str(old.id)]
    assert paged.json()["total"] == 2


@pytest.mark.asyncio
async def test_applied_other_lists_bank_import_recurring_installment(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food = test_categories[0].id
    now = datetime.now(timezone.utc)
    others = [
        _tx(session, test_user, test_account, f"LOJA {origin.upper()}", source=origin,
            category_id=food, created_at=now - timedelta(minutes=i))
        for i, origin in enumerate(("provider", "import", "recurring", "installment"))
    ]
    for origin in ("user", "legacy", "ai", "memory", "rule"):
        _tx(session, test_user, test_account, f"LOJA {origin.upper()}", source=origin, category_id=food)
    _tx(session, test_user, test_account, "SEM CATEGORIA", source="none")
    await session.commit()

    body = await _applied(client, auth_headers, "other")
    assert body["total"] == 4
    assert [i["transaction"]["id"] for i in body["items"]] == [str(tx.id) for tx in others]
    assert [i["source"] for i in body["items"]] == ["provider", "import", "recurring", "installment"]
    assert all(i["event_id"] is None for i in body["items"])


@pytest.mark.asyncio
async def test_applied_rejects_mixing_views(client, auth_headers):
    for source in ("ai,user", "confirmed,memory", "user,other", "other,rule", "bogus"):
        resp = await client.get(f"{API}/applied?source={source}", headers=auth_headers)
        assert resp.status_code == 422, (source, resp.text)
    assert (await client.get(f"{API}/applied?source=ai,memory,rule", headers=auth_headers)).status_code == 200


# --- scoreboard -------------------------------------------------------------------


async def _scoreboard(client, headers):
    resp = await client.get(f"{API}/scoreboard", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_scoreboard_sums_to_total(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food = test_categories[0].id
    pending = _tx(session, test_user, test_account, "PADARIA")
    _tx(session, test_user, test_account, "MERCADO", source="ai", category_id=food)
    _tx(session, test_user, test_account, "AGUARDANDO A IA")
    # Outside the base, even with a category or a pending suggestion.
    paired = _tx(session, test_user, test_account, "PARA CONTA", transfer_pair_id=uuid.uuid4())
    _tx(session, test_user, test_account, "AINDA PENDENTE", status="pending")
    _tx(session, test_user, test_account, "SALDO INICIAL", category_id=food, source="user",
        tx_source="opening_balance")
    _tx(session, test_user, test_account, "IGNORADA", category_id=food, source="user", is_ignored=True)
    await session.flush()
    _suggest(session, pending, "PADARIA", category_id=food)
    _suggest(session, paired, "PARA CONTA", category_id=food)
    await session.commit()

    board = await _scoreboard(client, auth_headers)
    assert board == {"total": 3, "decided": 1, "to_review": 1, "waiting": 1}
    assert board["decided"] + board["to_review"] + board["waiting"] == board["total"]


@pytest.mark.asyncio
async def test_scoreboard_categorized_row_with_pending_suggestion_is_to_review(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food, transport = test_categories[0].id, test_categories[1].id
    guessed = _tx(session, test_user, test_account, "UBER", source="provider", category_id=food)
    # A pending suggestion over a row the person categorized since is stale:
    # the row is decided, not up for review (it is not in Revisar either).
    by_hand = _tx(session, test_user, test_account, "UBER", source="user", category_id=transport)
    await session.flush()
    _suggest(session, guessed, "UBER", category_id=transport)
    _suggest(session, guessed, "UBER", category_id=transport)
    _suggest(session, by_hand, "UBER", category_id=transport)
    await session.commit()

    board = await _scoreboard(client, auth_headers)
    assert board == {"total": 2, "decided": 1, "to_review": 1, "waiting": 0}
    summary = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    assert summary["pending_transactions"] == board["to_review"]


@pytest.mark.asyncio
async def test_scoreboard_counts_transfer_like_categories_as_decided(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    # A category kept out of spending (`treat_as_transfer`, e.g. buying the
    # flat) is still a decision about an unpaired row.
    house = test_categories[1]
    house.treat_as_transfer = True
    _tx(session, test_user, test_account, "CONSTRUTORA", source="user", category_id=house.id)
    await session.commit()

    assert await _scoreboard(client, auth_headers) == {
        "total": 1, "decided": 1, "to_review": 0, "waiting": 0,
    }


@pytest.mark.asyncio
async def test_scoreboard_is_empty_for_a_new_workspace(client, auth_headers):
    assert await _scoreboard(client, auth_headers) == {
        "total": 0, "decided": 0, "to_review": 0, "waiting": 0,
    }


# --- what the person's actions applied ----------------------------------------------


@pytest.mark.asyncio
async def test_choose_and_accept_return_the_rows_applied_in_order(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food, transport = test_categories[0].id, test_categories[1].id
    first = _tx(session, test_user, test_account, "PADARIA ESTRELA")
    already = _tx(session, test_user, test_account, "PADARIA ESTRELA", source="user", category_id=food)
    second = _tx(session, test_user, test_account, "PADARIA ESTRELA")
    await session.commit()

    resp = await client.post(f"{API}/choose", headers=auth_headers, json={
        "merchant_key": "PADARIA ESTRELA",
        "category_id": str(food),
        "transaction_ids": [str(second.id), str(already.id), str(first.id)],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transaction_ids"] == [str(second.id), str(first.id)]
    assert body["applied"] == 2 and len(body["event_ids"]) == 2

    pending = _tx(session, test_user, test_account, "UBER")
    await session.flush()
    _suggest(session, pending, "UBER", category_id=transport)
    await session.commit()
    accepted = await client.post(f"{API}/review/accept", headers=auth_headers, json={
        "merchant_key": "UBER", "category_id": str(transport),
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["transaction_ids"] == [str(pending.id)]


@pytest.mark.asyncio
async def test_scoreboard_and_origin_rows_on_postgres(postgres_sessions):
    """The same counts and listing on PostgreSQL (EXISTS inside CASE inside
    SUM, a NOT DISTINCT FROM filter), where production runs them."""
    from types import SimpleNamespace

    from app.models.account import Account
    from app.services.categorization import review
    from tests.test_admin_service import _make_user

    async with postgres_sessions() as session:
        user = await _make_user(session, f"board_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.commit()
        acct = SimpleNamespace(workspace_id=account.workspace_id, id=account.id)
        category = Category(id=uuid.uuid4(), user_id=user.id, name="Mercado", icon="cart", color="#000000")
        session.add(category)
        await session.flush()
        category_id = category.id
        pending = _tx(session, user, acct, "PADARIA")
        stale = _tx(session, user, acct, "UBER", source="user", category_id=category_id)
        bank = _tx(session, user, acct, "MERCADO", source="provider", category_id=category_id)
        _tx(session, user, acct, "AGUARDANDO")
        _tx(session, user, acct, "PARA CONTA", transfer_pair_id=uuid.uuid4())
        await session.flush()
        _suggest(session, pending, "PADARIA")
        _suggest(session, stale, "UBER")
        confirmation = _event(session, bank, source="memory", previous=category_id, new=category_id)
        await session.commit()
        ws = account.workspace_id

        assert await review.scoreboard(session, ws) == {
            "total": 4, "decided": 2, "to_review": 1, "waiting": 1,
        }
        items, total = await review.origin_rows(
            session, ws, origins=review.OTHER_ORIGINS, limit=10, offset=0
        )
        assert total == 1 and [i["event_id"] for i in items] == [confirmation.id]
        items, total = await review.applied_events(session, ws, confirmed=True)
        assert total == 1 and [i["transaction"]["id"] for i in items] == [bank.id]


# --- review round: shared base, order, superseded events --------------------------


@pytest.mark.asyncio
async def test_applied_confirmed_leaves_out_confirming_no_category(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food = test_categories[0].id
    kept = _tx(session, test_user, test_account, "PADARIA", source="ai", category_id=food)
    empty = _tx(session, test_user, test_account, "SEM NADA", source="ai")
    await session.flush()
    _event(session, kept, source="ai", previous=food, new=food)
    _event(session, empty, source="ai", previous=None, new=None)
    await session.commit()

    body = await _applied(client, auth_headers, "confirmed")
    assert [i["transaction"]["id"] for i in body["items"]] == [str(kept.id)]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_superseded_events_leave_both_event_views(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    """An event a later standing decision on the same row replaced shows a
    category the row may no longer have, and its undo would be skipped."""
    food, transport = test_categories[0].id, test_categories[1].id
    now = datetime.now(timezone.utc)
    changed = _tx(session, test_user, test_account, "UBER", source="user", category_id=food)
    confirmed = _tx(session, test_user, test_account, "MERCADO", source="user", category_id=transport)
    still = _tx(session, test_user, test_account, "POSTO", source="ai", category_id=transport)
    reopened = _tx(session, test_user, test_account, "FEIRA", source="memory", category_id=food)
    await session.flush()
    _event(session, changed, source="ai", previous=None, new=transport, created_at=now - timedelta(hours=2))
    _event(session, changed, source="user", previous=transport, new=food, created_at=now - timedelta(hours=1))
    _event(session, confirmed, source="memory", previous=food, new=food, created_at=now - timedelta(hours=2))
    _event(session, confirmed, source="user", previous=food, new=transport, created_at=now - timedelta(hours=1))
    kept_change = _event(session, still, source="ai", previous=None, new=transport)
    # A later decision that was undone supersedes nothing.
    kept_confirmation = _event(session, reopened, source="memory", previous=food, new=food,
                               created_at=now - timedelta(hours=2))
    gone = _event(session, reopened, source="user", previous=food, new=transport,
                  created_at=now - timedelta(hours=1))
    gone.undone_at = now
    await session.commit()

    default = (await client.get(f"{API}/applied", headers=auth_headers)).json()
    assert [i["event_id"] for i in default["items"]] == [str(kept_change.id)]
    assert default["total"] == 1
    summary = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    assert summary["applied_last_7d"] == default["total"]
    body = await _applied(client, auth_headers, "confirmed")
    assert [i["event_id"] for i in body["items"]] == [str(kept_confirmation.id)]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_pending_over_rows_that_left_the_base_is_not_counted_anywhere(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    """A pending suggestion whose row later became one side of a transfer,
    ignored or an opening balance asks nothing: Revisar, its badge and the
    scoreboard leave it out, and accepting the store leaves the row alone."""
    food = test_categories[0].id
    open_tx = _tx(session, test_user, test_account, "PADARIA")
    paired = _tx(session, test_user, test_account, "PADARIA", transfer_pair_id=uuid.uuid4())
    ignored = _tx(session, test_user, test_account, "PADARIA", is_ignored=True)
    opening = _tx(session, test_user, test_account, "PADARIA", tx_source="opening_balance")
    await session.flush()
    for tx in (open_tx, paired, ignored, opening):
        _suggest(session, tx, "PADARIA", category_id=food)
    await session.commit()

    board = await _scoreboard(client, auth_headers)
    summary = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    queue = (await client.get(f"{API}/review", headers=auth_headers)).json()
    assert board["to_review"] == 1
    assert summary["pending_transactions"] == board["to_review"]
    assert summary["pending_groups"] == 1
    assert [g["transaction_ids"] for g in queue["groups"]] == [[str(open_tx.id)]]

    accepted = await client.post(f"{API}/review/accept", headers=auth_headers, json={
        "merchant_key": "PADARIA", "category_id": str(food),
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["transaction_ids"] == [str(open_tx.id)]
    for tx in (paired, ignored, opening):
        await session.refresh(tx)
        assert tx.category_id is None and tx.category_source == "none"
    after = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    assert after["pending_transactions"] == 0


@pytest.mark.asyncio
async def test_accept_returns_rows_in_review_or_request_order(
    client, auth_headers, session, test_user, test_account, test_categories,
):
    food = test_categories[0].id
    now = datetime.now(timezone.utc)
    rows = [_tx(session, test_user, test_account, "PADARIA") for _ in range(4)]
    await session.flush()
    for i, tx in enumerate(rows):
        sug = _suggest(session, tx, "PADARIA", category_id=food)
        sug.created_at = now - timedelta(minutes=i * 7 % 4)  # not the insertion order
    await session.commit()

    group = (await client.get(f"{API}/review", headers=auth_headers)).json()["groups"][0]
    chosen = [group["transaction_ids"][2], group["transaction_ids"][0]]
    picked = await client.post(f"{API}/review/accept", headers=auth_headers, json={
        "merchant_key": "PADARIA", "category_id": str(food), "transaction_ids": chosen,
    })
    assert picked.status_code == 200, picked.text
    assert picked.json()["transaction_ids"] == chosen

    rest = [t for t in group["transaction_ids"] if t not in chosen]
    accepted = await client.post(f"{API}/review/accept", headers=auth_headers, json={
        "merchant_key": "PADARIA", "category_id": str(food),
    })
    assert accepted.json()["transaction_ids"] == rest
