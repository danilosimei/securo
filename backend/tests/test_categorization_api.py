"""The categorization API: review queue, audit list, undo, summary, settings, run."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agents.models.connection import LlmConnection
from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSettings,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.services.categorization import costs
from app.services.categorization.settings import find_ai_connection, get_settings

API = "/api/categorization"


def _tx(
    session, user, account, description, *,
    source="none", category_id=None, amount="10.00", day=date(2026, 9, 10),
    payee_id=None, **extra,
):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal(amount),
        currency="BRL",
        date=day,
        effective_date=day,
        type=extra.pop("type", "debit"),
        source="sync",
        status=extra.pop("status", "posted"),
        category_id=category_id,
        category_source=source,
        payee_id=payee_id,
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    return tx


def _suggest(session, tx, key, *, category_id=None, alt=None, confidence=70,
             reason="parece mercado", clean_name=None, new_category=None):
    sug = CategorizationSuggestion(
        id=uuid.uuid4(),
        workspace_id=tx.workspace_id,
        transaction_id=tx.id,
        merchant_key=key,
        clean_name=clean_name,
        category_id=category_id,
        alt_category_id=alt,
        confidence=confidence,
        reason=reason,
        new_category=new_category,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    session.add(sug)
    return sug


def _event(session, tx, *, source="ai", previous=None, new=None, confidence=95,
           run_id=None, created_at=None, reason="mercado"):
    ev = CategorizationEvent(
        id=uuid.uuid4(),
        workspace_id=tx.workspace_id,
        transaction_id=tx.id,
        run_id=run_id,
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


async def _fresh(session, model, pk):
    return await session.get(model, pk, populate_existing=True)


async def _member(session, client, workspace, role, email):
    import bcrypt

    user = User(
        id=uuid.uuid4(), email=email,
        hashed_password=bcrypt.hashpw(b"pass12345", bcrypt.gensalt()).decode(),
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(user)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role=role))
    await session.commit()
    resp = await client.post(
        "/api/auth/login", data={"username": email, "password": "pass12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}", "X-Workspace-Id": str(workspace.id)}


# --- costs / connection ------------------------------------------------------


@pytest.mark.asyncio
async def test_month_spend_sums_current_month_only(session, test_workspace):
    now = datetime.now(timezone.utc)
    for cost, created in (
        ("0.10", now),
        ("0.05", now),
        ("0.70", now - timedelta(days=45)),
    ):
        session.add(CategorizationRun(
            id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify",
            trigger="ingest", status="done", cost_usd=Decimal(cost), created_at=created,
            stats={}, detail={},
        ))
    await session.commit()
    assert await costs.month_spend(session, test_workspace.id) == Decimal("0.15")


@pytest.mark.asyncio
async def test_month_spend_is_zero_without_runs(session, test_workspace):
    assert await costs.month_spend(session, test_workspace.id) == Decimal("0")


@pytest.mark.asyncio
async def test_find_ai_connection(session, test_user, test_workspace):
    settings = await get_settings(session, test_workspace.id)
    assert await find_ai_connection(session, settings) is None

    other_kind = LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="o", kind="ollama", is_default=True, extra={})
    not_default = LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="a", kind="anthropic", is_default=False, extra={})
    session.add_all([other_kind, not_default])
    await session.flush()
    assert await find_ai_connection(session, settings) is None

    default = LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="b", kind="anthropic", is_default=True, extra={})
    session.add(default)
    await session.flush()
    found = await find_ai_connection(session, settings)
    assert found is not None and found.id == default.id

    settings.llm_connection_id = not_default.id
    await session.flush()
    found = await find_ai_connection(session, settings)
    assert found is not None and found.id == not_default.id


# --- summary -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary(client, auth_headers, session, test_user, test_account, test_categories):
    a = _tx(session, test_user, test_account, "PADARIA")
    b = _tx(session, test_user, test_account, "PADARIA")
    c = _tx(session, test_user, test_account, "POSTO SHELL")
    applied = _tx(session, test_user, test_account, "UBER", source="ai", category_id=test_categories[1].id)
    await session.flush()
    _suggest(session, a, "PADARIA")
    _suggest(session, b, "PADARIA")
    _suggest(session, c, "POSTO SHELL")
    _event(session, applied, new=test_categories[1].id)
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_account.workspace_id, kind="classify",
        trigger="ingest", status="done", cost_usd=Decimal("0.25"), stats={"applied": 1},
        detail={}, finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.commit()

    resp = await client.get(f"{API}/summary", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pending_groups"] == 2
    assert body["pending_transactions"] == 3
    assert body["applied_last_7d"] == 1
    assert body["last_run"]["id"] == str(run.id)
    assert body["last_run"]["stats"] == {"applied": 1}
    assert body["ai_available"] is False
    assert Decimal(str(body["month_cost_usd"])) == Decimal("0.25")
    assert Decimal(str(body["monthly_cap_usd"])) == Decimal("1.00")

    session.add(LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="c", kind="anthropic", is_default=True, extra={}))
    await session.commit()
    assert (await client.get(f"{API}/summary", headers=auth_headers)).json()["ai_available"] is True


# --- review ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_groups_sorted_by_total(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    small = _tx(session, test_user, test_account, "PADARIA DO ZE", amount="5.00")
    big1 = _tx(session, test_user, test_account, "MERCADO X 123", amount="100.00")
    big2 = _tx(session, test_user, test_account, "MERCADO X 456", amount="50.00")
    pix = _tx(session, test_user, test_account, "Transferencia enviada|MARIA SILVA", amount="20.00")
    await session.flush()
    _suggest(session, small, "PADARIA DO ZE", category_id=food)
    _suggest(session, big1, "MERCADO X", category_id=food, alt=test_categories[1].id, confidence=80, clean_name="Mercado X")
    _suggest(session, big2, "MERCADO X", category_id=food, alt=test_categories[1].id, confidence=80, clean_name="Mercado X")
    _suggest(session, pix, "PIX:MARIA SILVA", category_id=None, confidence=None, reason="sem IA")
    await session.commit()

    resp = await client.get(f"{API}/review", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    groups = body["groups"]
    assert [g["merchant_key"] for g in groups] == ["MERCADO X", "PIX:MARIA SILVA", "PADARIA DO ZE"]
    top = groups[0]
    assert top["clean_name"] == "Mercado X"
    assert top["count"] == 2
    assert Decimal(str(top["total"])) == Decimal("150.00")
    assert sorted(top["transaction_ids"]) == sorted([str(big1.id), str(big2.id)])
    assert top["suggestion"] == {
        "category_id": str(food), "alt_category_id": str(test_categories[1].id),
        "confidence": 80, "reason": "parece mercado",
    }
    assert top["is_person"] is False
    assert top["samples"]
    assert groups[1]["is_person"] is True
    assert groups[1]["suggestion"] is None
    assert body["new_category_proposals"] == []

    page = (await client.get(f"{API}/review?limit=1&offset=1", headers=auth_headers)).json()
    assert [g["merchant_key"] for g in page["groups"]] == ["PIX:MARIA SILVA"]


@pytest.mark.asyncio
async def test_review_new_category_proposals_need_three_stores(client, auth_headers, session, test_user, test_account):
    for i, (group, name) in enumerate((("Pets", "Veterinário"), ("pets ", "veterinario"), ("PETS", "Veterinario"))):
        tx = _tx(session, test_user, test_account, f"VET {i}", amount="10.00")
        await session.flush()
        _suggest(session, tx, f"VET{i}", new_category={"group": group, "name": name})
    lone = _tx(session, test_user, test_account, "ACADEMIA")
    await session.flush()
    _suggest(session, lone, "ACADEMIA", new_category={"group": "Saúde", "name": "Academia"})
    await session.commit()

    body = (await client.get(f"{API}/review", headers=auth_headers)).json()
    proposals = body["new_category_proposals"]
    assert len(proposals) == 1
    assert proposals[0]["group"] == "Pets"
    assert proposals[0]["name"] == "Veterinário"
    assert sorted(proposals[0]["merchant_keys"]) == ["VET0", "VET1", "VET2"]
    assert proposals[0]["count"] == 3
    assert Decimal(str(proposals[0]["total"])) == Decimal("30.00")
    vet = next(g for g in body["groups"] if g["merchant_key"] == "VET0")
    assert vet["new_category"] == {"group": "Pets", "name": "Veterinário"}


@pytest.mark.asyncio
async def test_accept_learns_memory_and_marks_user(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    txs = [_tx(session, test_user, test_account, f"PADARIA {i}") for i in range(3)]
    other = _tx(session, test_user, test_account, "OUTRA")
    await session.flush()
    sugs = [_suggest(session, tx, "PADARIA", category_id=food) for tx in txs]
    other_sug = _suggest(session, other, "OUTRA", category_id=food)
    await session.commit()

    resp = await client.post(
        f"{API}/review/accept", headers=auth_headers,
        json={"merchant_key": "PADARIA", "category_id": str(food)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == 3
    assert len(body["event_ids"]) == 3

    for tx in txs:
        fresh = await _fresh(session, Transaction, tx.id)
        assert fresh.category_id == food
        assert fresh.category_source == "user"
    for sug in sugs:
        fresh = await _fresh(session, CategorizationSuggestion, sug.id)
        assert fresh.status == "accepted"
        assert fresh.resolved_at is not None
    assert (await _fresh(session, CategorizationSuggestion, other_sug.id)).status == "pending"

    memory = (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == "PADARIA")
    )).scalar_one()
    await session.refresh(memory)
    # One decision, one count: not one per transaction.
    assert memory.counts == {str(food): 1}

    events = (await session.execute(
        select(CategorizationEvent).where(CategorizationEvent.id.in_([uuid.UUID(e) for e in body["event_ids"]]))
    )).scalars().all()
    assert {e.source for e in events} == {"user"}
    assert {e.user_id for e in events} == {test_user.id}

    undo = await client.post(f"{API}/undo", headers=auth_headers, json={"event_ids": body["event_ids"]})
    assert undo.json() == {"restored": 3, "skipped": 0}

    # Undo takes the decision back: the store is in review again, the memory forgets.
    for sug in sugs:
        fresh = await _fresh(session, CategorizationSuggestion, sug.id)
        assert (fresh.status, fresh.resolved_at) == ("pending", None)
    review = (await client.get(f"{API}/review", headers=auth_headers)).json()
    padaria = next(g for g in review["groups"] if g["merchant_key"] == "PADARIA")
    assert padaria["count"] == 3
    await session.refresh(memory)
    assert memory.counts == {}


@pytest.mark.asyncio
async def test_undo_after_similar_apply_unlearns(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    ref = _tx(session, test_user, test_account, "IFOOD 1", source="ai", category_id=food)
    other = _tx(session, test_user, test_account, "IFOOD 2", source="none")
    await session.commit()
    applied = (await client.post(f"{API}/similar/apply", headers=auth_headers, json={"transaction_id": str(ref.id)})).json()
    memory = (await session.execute(select(MerchantMemory).where(MerchantMemory.merchant_key == "IFOOD"))).scalar_one()
    assert memory.counts == {str(food): 1}
    undo = await client.post(f"{API}/undo", headers=auth_headers, json={"event_ids": applied["event_ids"]})
    assert undo.json() == {"restored": 1, "skipped": 0}
    await session.refresh(memory)
    assert memory.counts == {}
    assert (await _fresh(session, Transaction, other.id)).category_source == "none"


async def _counts(session, key):
    row = (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == key)
    )).scalar_one_or_none()
    if row is None:
        return None
    await session.refresh(row)
    return row.counts


async def _event_ids(session, tx_ids, source):
    return (await session.execute(
        select(CategorizationEvent.id)
        .where(CategorizationEvent.transaction_id.in_(tx_ids), CategorizationEvent.source == source)
        .order_by(CategorizationEvent.created_at)
    )).scalars().all()


async def _undo(client, auth_headers, ids):
    resp = await client.post(f"{API}/undo", headers=auth_headers, json={"event_ids": [str(i) for i in ids]})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_undoing_a_persons_clear_gives_the_store_its_pick_back(client, auth_headers, session, test_user, test_account, test_categories):
    """B4-X: undoing a person's clear gives back exactly the pick the clear
    took (R27-U). A bulk clear took one per store, so undoing its rows one
    by one gives back one, not one per row."""
    from app.services.categorization.memory import learn_choice
    from app.services.categorization.merchant_key import merchant_key

    food = test_categories[0].id
    key = merchant_key("PADARIA PAO QUENTE")
    for _ in range(2):
        await learn_choice(session, test_account.workspace_id, key, food)
    a = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="user", category_id=food)
    b = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="user", category_id=food)
    await session.commit()

    resp = await client.patch(
        "/api/transactions/bulk-categorize", headers=auth_headers,
        json={"transaction_ids": [str(a.id), str(b.id)], "category_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert await _counts(session, key) == {str(food): 1}
    clears = await _event_ids(session, [a.id, b.id], "none")
    assert len(clears) == 2

    for clear in clears:
        assert await _undo(client, auth_headers, [clear]) == {"restored": 1, "skipped": 0}

    fresh = await _fresh(session, Transaction, a.id)
    assert (fresh.category_id, fresh.category_source) == (food, "user")
    assert await _counts(session, key) == {str(food): 2}
    memory = (await session.execute(select(MerchantMemory).where(MerchantMemory.merchant_key == key))).scalar_one()
    assert memory.last_user_category_id == food


@pytest.mark.asyncio
async def test_undoing_two_single_clears_at_once_gives_both_picks_back(client, auth_headers, session, test_user, test_account, test_categories):
    """Two clears took two picks: one undo naming both gives both back."""
    from app.services.categorization.memory import learn_choice
    from app.services.categorization.merchant_key import merchant_key

    food = test_categories[0].id
    key = merchant_key("PADARIA PAO QUENTE")
    for _ in range(2):
        await learn_choice(session, test_account.workspace_id, key, food)
    a = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="user", category_id=food)
    b = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="user", category_id=food)
    await session.commit()
    for tx in (a, b):
        resp = await client.patch(f"/api/transactions/{tx.id}", headers=auth_headers, json={"category_id": None})
        assert resp.status_code == 200, resp.text
    assert await _counts(session, key) == {}

    assert await _undo(client, auth_headers, await _event_ids(session, [a.id, b.id], "none")) == {
        "restored": 2, "skipped": 0,
    }

    assert await _counts(session, key) == {str(food): 2}


@pytest.mark.asyncio
async def test_undoing_a_clear_that_took_no_pick_teaches_nothing(client, auth_headers, session, test_user, test_account, test_categories):
    """Clearing a guess the store never had a pick for took nothing (the
    floor at zero), so its undo gives nothing: no phantom pick."""
    from app.services.categorization.merchant_key import merchant_key

    food = test_categories[0].id
    tx = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="ai", category_id=food)
    await session.commit()
    resp = await client.patch(f"/api/transactions/{tx.id}", headers=auth_headers, json={"category_id": None})
    assert resp.status_code == 200, resp.text

    assert await _undo(client, auth_headers, await _event_ids(session, [tx.id], "none")) == {
        "restored": 1, "skipped": 0,
    }

    assert (await _fresh(session, Transaction, tx.id)).category_source == "ai"
    assert await _counts(session, merchant_key("PADARIA PAO QUENTE")) is None


@pytest.mark.asyncio
async def test_undoing_a_derived_sibling_clear_teaches_nothing(client, auth_headers, session, test_user, test_workspace, test_account, test_categories):
    """A series clear takes one pick, through the row the person edited;
    a sibling's clear only followed it, so undoing it alone gives nothing,
    and undoing the edited row's clear gives the one pick back."""
    from app.schemas.transaction import InstallmentSeriesCreate, TransactionCreate, TransactionUpdate
    from app.services import transaction_service
    from app.services.categorization.memory import learn_choice

    food = test_categories[0].id
    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="SOFA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=food,
            ),
            installments=3,
        ),
    )
    [key] = (await session.execute(select(MerchantMemory.merchant_key))).scalars().all()
    await learn_choice(session, test_workspace.id, key, food)
    await session.commit()
    assert await _counts(session, key) == {str(food): 2}

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=None, apply_to="all"),
    )
    assert await _counts(session, key) == {str(food): 1}
    [sibling_clear] = await _event_ids(session, [created[1].id], "none")
    [anchor_clear] = await _event_ids(session, [created[0].id], "none")

    assert await _undo(client, auth_headers, [sibling_clear]) == {"restored": 1, "skipped": 0}
    assert await _counts(session, key) == {str(food): 1}

    assert await _undo(client, auth_headers, [anchor_clear]) == {"restored": 1, "skipped": 0}
    assert await _counts(session, key) == {str(food): 2}


@pytest.mark.asyncio
async def test_undoing_a_series_clear_of_the_siblings_only_gives_its_pick_back(client, auth_headers, session, test_user, test_workspace, test_account, test_categories):
    """The anchor is already empty and only the siblings lose the category
    (the clear is taken back before the edit): the pick still comes back
    through the sibling it was taken for, once."""
    from app.schemas.transaction import InstallmentSeriesCreate, TransactionCreate, TransactionUpdate
    from app.services import transaction_service
    from app.services.categorization.memory import learn_choice

    food = test_categories[0].id
    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="SOFA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=food,
            ),
            installments=3,
        ),
    )
    [key] = (await session.execute(select(MerchantMemory.merchant_key))).scalars().all()
    for _ in range(2):
        await learn_choice(session, test_workspace.id, key, food)
    await session.commit()
    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id, TransactionUpdate(category_id=None),
    )
    assert await _counts(session, key) == {str(food): 2}

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=None, apply_to="all"),
    )
    assert await _counts(session, key) == {str(food): 1}

    sibling_clears = await _event_ids(session, [created[1].id, created[2].id], "none")
    assert len(sibling_clears) == 2
    for event_id in sibling_clears:
        assert await _undo(client, auth_headers, [event_id]) == {"restored": 1, "skipped": 0}
    assert await _counts(session, key) == {str(food): 2}


@pytest.mark.asyncio
async def test_undoing_a_series_pick_that_moved_only_the_siblings_takes_back_its_pick(client, auth_headers, session, test_user, test_workspace, test_account, test_categories):
    """X-1: the anchor already had the category and only its siblings moved.
    The memory learned once, from the anchor; undoing the siblings one by one
    takes that one pick back, no more."""
    from app.schemas.transaction import InstallmentSeriesCreate, TransactionCreate, TransactionUpdate
    from app.services import transaction_service

    food, transport = test_categories[0].id, test_categories[1].id
    created = await transaction_service.create_installment_series(
        session, test_workspace.id, test_user.id,
        InstallmentSeriesCreate(
            base=TransactionCreate(
                account_id=test_account.id, description="SOFA", amount=Decimal("100"),
                date=date(2026, 1, 10), type="debit", category_id=food,
            ),
            installments=3,
        ),
    )
    [key] = (await session.execute(select(MerchantMemory.merchant_key))).scalars().all()
    for row in created[1:]:
        # Moved off the series without an event (as a merge would).
        (await _fresh(session, Transaction, row.id)).category_id = transport
    await session.commit()
    assert await _counts(session, key) == {str(food): 1}

    await transaction_service.update_transaction(
        session, created[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(category_id=food, apply_to="all"),
    )
    assert await _counts(session, key) == {str(food): 2}

    sibling_picks = (await session.execute(
        select(CategorizationEvent.id).where(
            CategorizationEvent.transaction_id.in_([row.id for row in created[1:]]),
            CategorizationEvent.previous_category_id == transport,
            CategorizationEvent.new_category_id == food,
        )
    )).scalars().all()
    assert len(sibling_picks) == 2
    for event_id in sibling_picks:
        assert await _undo(client, auth_headers, [event_id]) == {"restored": 1, "skipped": 0}

    assert await _counts(session, key) == {str(food): 1}


@pytest.mark.asyncio
async def test_undoing_an_accept_row_by_row_takes_back_only_its_pick(client, auth_headers, session, test_user, test_account, test_categories):
    """m4: accepting a store teaches one pick; undoing its rows one by one
    takes back that one pick, not one per row (a pick taught before stays)."""
    from app.services.categorization.memory import learn_choice

    food = test_categories[0].id
    await learn_choice(session, test_account.workspace_id, "PADARIA", food)
    txs = [_tx(session, test_user, test_account, "PADARIA") for _ in range(2)]
    await session.flush()
    for tx in txs:
        _suggest(session, tx, "PADARIA", category_id=food)
    await session.commit()

    body = (await client.post(
        f"{API}/review/accept", headers=auth_headers,
        json={"merchant_key": "PADARIA", "category_id": str(food)},
    )).json()
    assert await _counts(session, "PADARIA") == {str(food): 2}

    for event_id in body["event_ids"]:
        assert await _undo(client, auth_headers, [event_id]) == {"restored": 1, "skipped": 0}

    assert await _counts(session, "PADARIA") == {str(food): 1}


@pytest.mark.asyncio
async def test_undoing_a_bulk_pick_row_by_row_takes_back_only_its_pick(client, auth_headers, session, test_user, test_account, test_categories):
    """m4 on the bulk edit: it taught one pick per store, so undoing its
    rows one by one takes back one."""
    from app.services.categorization.memory import learn_choice
    from app.services.categorization.merchant_key import merchant_key

    food = test_categories[0].id
    key = merchant_key("PADARIA PAO QUENTE")
    await learn_choice(session, test_account.workspace_id, key, food)
    txs = [_tx(session, test_user, test_account, "PADARIA PAO QUENTE") for _ in range(2)]
    await session.commit()
    resp = await client.patch(
        "/api/transactions/bulk-categorize", headers=auth_headers,
        json={"transaction_ids": [str(tx.id) for tx in txs], "category_id": str(food)},
    )
    assert resp.status_code == 200, resp.text
    assert await _counts(session, key) == {str(food): 2}

    for event_id in await _event_ids(session, [tx.id for tx in txs], "user"):
        assert await _undo(client, auth_headers, [event_id]) == {"restored": 1, "skipped": 0}

    assert await _counts(session, key) == {str(food): 1}


@pytest.mark.asyncio
async def test_undoing_a_pipeline_empty_guess_teaches_nothing(client, auth_headers, session, test_user, test_account, test_categories):
    """Only a person's clear gave a pick away: an event `none` with no
    person behind it is undone without touching the memory."""
    from app.services.categorization.merchant_key import merchant_key

    food = test_categories[0].id
    tx = _tx(session, test_user, test_account, "PADARIA PAO QUENTE")
    await session.flush()
    ev = _event(session, tx, source="none", previous=food, new=None)
    await session.commit()

    undo = await client.post(f"{API}/undo", headers=auth_headers, json={"event_ids": [str(ev.id)]})

    assert undo.json() == {"restored": 1, "skipped": 0}
    assert (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == merchant_key("PADARIA PAO QUENTE"))
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_accept_only_selected_transactions(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    a = _tx(session, test_user, test_account, "PADARIA")
    b = _tx(session, test_user, test_account, "PADARIA")
    await session.flush()
    _suggest(session, a, "PADARIA", category_id=food)
    sb = _suggest(session, b, "PADARIA", category_id=food)
    await session.commit()

    resp = await client.post(
        f"{API}/review/accept", headers=auth_headers,
        json={"merchant_key": "PADARIA", "category_id": str(food), "transaction_ids": [str(a.id)]},
    )
    assert resp.json()["applied"] == 1
    assert (await _fresh(session, Transaction, b.id)).category_id is None
    assert (await _fresh(session, CategorizationSuggestion, sb.id)).status == "pending"


@pytest.mark.asyncio
async def test_accept_keeps_rows_categorized_by_hand_since(client, auth_headers, session, test_user, test_account, test_categories):
    food, transport = test_categories[0].id, test_categories[1].id
    a = _tx(session, test_user, test_account, "PADARIA")
    manual = _tx(session, test_user, test_account, "PADARIA", source="user", category_id=transport)
    await session.flush()
    _suggest(session, a, "PADARIA", category_id=food)
    sm = _suggest(session, manual, "PADARIA", category_id=food)
    await session.commit()

    resp = await client.post(
        f"{API}/review/accept", headers=auth_headers,
        json={"merchant_key": "PADARIA", "category_id": str(food)},
    )
    assert resp.json()["applied"] == 1
    assert (await _fresh(session, Transaction, manual.id)).category_id == transport
    assert (await _fresh(session, CategorizationSuggestion, sm.id)).status == "accepted"


@pytest.mark.asyncio
async def test_accept_never_overrides_a_hand_choice_even_when_selected(client, auth_headers, session, test_user, test_account, test_categories):
    food, transport = test_categories[0].id, test_categories[1].id
    manual = _tx(session, test_user, test_account, "PADARIA", source="user", category_id=transport)
    await session.flush()
    sm = _suggest(session, manual, "PADARIA", category_id=food)
    await session.commit()

    resp = await client.post(
        f"{API}/review/accept", headers=auth_headers,
        json={"merchant_key": "PADARIA", "category_id": str(food), "transaction_ids": [str(manual.id)]},
    )
    assert resp.json()["applied"] == 0
    fresh = await _fresh(session, Transaction, manual.id)
    assert (fresh.category_id, fresh.category_source) == (transport, "user")
    assert (await _fresh(session, CategorizationSuggestion, sm.id)).status == "accepted"
    # Nothing was filed, so there is no event to carry a pick: none is taught.
    memory = (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == "PADARIA")
    )).scalar_one_or_none()
    assert memory is None or memory.counts == {}


@pytest.mark.asyncio
async def test_review_and_summary_ignore_suggestions_over_protected_rows(client, auth_headers, session, test_user, test_account, test_categories):
    open_tx = _tx(session, test_user, test_account, "PADARIA")
    manual = _tx(session, test_user, test_account, "PADARIA", source="user", category_id=test_categories[1].id)
    legacy = _tx(session, test_user, test_account, "POSTO", source="legacy", category_id=test_categories[1].id)
    await session.flush()
    _suggest(session, open_tx, "PADARIA")
    _suggest(session, manual, "PADARIA")
    _suggest(session, legacy, "POSTO")
    await session.commit()

    review = (await client.get(f"{API}/review", headers=auth_headers)).json()
    assert [(g["merchant_key"], g["count"]) for g in review["groups"]] == [("PADARIA", 1)]
    assert review["groups"][0]["transaction_ids"] == [str(open_tx.id)]
    summary = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    assert (summary["pending_groups"], summary["pending_transactions"]) == (1, 1)


@pytest.mark.asyncio
async def test_review_group_carries_its_first_transactions(client, auth_headers, session, test_user, test_account):
    """Opening a store in Revisar costs no request per row: the queue
    carries the store's first rows (T12-1)."""
    from app.services.categorization.review import REVIEW_PREVIEW

    rows = [
        _tx(session, test_user, test_account, f"MERCADO X {n}", amount=f"{n + 1}.00",
            day=date(2026, 9, n + 1), original_description=f"MERCADO X LTDA {n}")
        for n in range(REVIEW_PREVIEW + 2)
    ]
    refund = _tx(session, test_user, test_account, "MERCADO X ESTORNO", amount="7.00", type="credit")
    await session.flush()
    for tx in [*rows, refund]:
        _suggest(session, tx, "MERCADO X")
    await session.commit()

    [store] = (await client.get(f"{API}/review", headers=auth_headers)).json()["groups"]

    assert store["count"] == REVIEW_PREVIEW + 3
    assert len(store["transactions"]) == REVIEW_PREVIEW
    assert [t["id"] for t in store["transactions"]] == store["transaction_ids"][:REVIEW_PREVIEW]
    # Newest suggestion first: the refund leads.
    assert store["transactions"][0] == {
        "id": str(refund.id),
        "date": "2026-09-10",
        "description": "MERCADO X ESTORNO",
        "original_description": None,
        "amount": "7.00",
        "currency": "BRL",
        "type": "credit",
    }
    last = rows[-1]
    first_debit = store["transactions"][1]
    assert (first_debit["id"], first_debit["original_description"], first_debit["type"]) == (
        str(last.id), last.original_description, "debit",
    )


@pytest.mark.asyncio
async def test_accept_rejects_foreign_category(client, auth_headers, session, test_user, test_account):
    tx = _tx(session, test_user, test_account, "PADARIA")
    await session.flush()
    _suggest(session, tx, "PADARIA")
    await session.commit()
    resp = await client.post(
        f"{API}/review/accept", headers=auth_headers,
        json={"merchant_key": "PADARIA", "category_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dismiss(client, auth_headers, session, test_user, test_account):
    tx = _tx(session, test_user, test_account, "PADARIA")
    await session.flush()
    sug = _suggest(session, tx, "PADARIA")
    await session.commit()
    resp = await client.post(f"{API}/review/dismiss", headers=auth_headers, json={"merchant_key": "PADARIA"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dismissed": 1}
    fresh = await _fresh(session, CategorizationSuggestion, sug.id)
    assert fresh.status == "dismissed"
    assert fresh.resolved_at is not None
    assert (await _fresh(session, Transaction, tx.id)).category_source == "none"


@pytest.mark.asyncio
async def test_create_category_applies_to_stores(client, auth_headers, session, test_user, test_account, test_workspace):
    txs = []
    for key in ("VET1", "VET2"):
        tx = _tx(session, test_user, test_account, key)
        txs.append(tx)
        await session.flush()
        _suggest(session, tx, key, new_category={"group": "Pets", "name": "Veterinário"})
    untouched = _tx(session, test_user, test_account, "OUTRA")
    await session.flush()
    _suggest(session, untouched, "OUTRA")
    session.add(MerchantMemory(
        id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="ELSEWHERE", counts={},
        ai_category_id=None, ai_confidence=91, ai_clean_name="x",
    ))
    await session.commit()

    resp = await client.post(
        f"{API}/review/create-category", headers=auth_headers,
        json={"group": "Pets", "name": "Veterinário", "merchant_keys": ["VET1", "VET2"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == 2
    category = await _fresh(session, Category, uuid.UUID(body["category_id"]))
    assert category.name == "Veterinário"
    assert category.workspace_id == test_workspace.id
    group = await _fresh(session, CategoryGroup, category.group_id)
    assert group.name == "Pets"
    assert group.workspace_id == test_workspace.id
    for tx in txs:
        fresh = await _fresh(session, Transaction, tx.id)
        assert fresh.category_id == category.id
        assert fresh.category_source == "user"
    assert (await _fresh(session, Transaction, untouched.id)).category_id is None
    other = (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == "ELSEWHERE")
    )).scalar_one()
    await session.refresh(other)
    assert other.ai_confidence is None

    # A hidden category of the same name is not reused: a new one is made.
    category.is_hidden = True
    await session.commit()
    hidden_reuse = await client.post(
        f"{API}/review/create-category", headers=auth_headers,
        json={"group": "Pets", "name": "Veterinário", "merchant_keys": []},
    )
    assert hidden_reuse.status_code == 200, hidden_reuse.text
    assert hidden_reuse.json()["category_id"] != str(category.id)

    # Same group again: reused, not duplicated.
    again = await client.post(
        f"{API}/review/create-category", headers=auth_headers,
        json={"group": "pets", "name": "Ração", "merchant_keys": []},
    )
    assert again.status_code == 200, again.text
    groups = (await session.execute(
        select(CategoryGroup).where(CategoryGroup.workspace_id == test_workspace.id, CategoryGroup.name.ilike("pets"))
    )).scalars().all()
    assert len(groups) == 1


# --- applied / undo / provenance ----------------------------------------------


@pytest.mark.asyncio
async def test_applied_lists_automatic_events(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    t1 = _tx(session, test_user, test_account, "A", source="ai", category_id=food, amount="12.00")
    t2 = _tx(session, test_user, test_account, "B", source="memory", category_id=food)
    t3 = _tx(session, test_user, test_account, "C", source="user", category_id=food)
    t4 = _tx(session, test_user, test_account, "D", source="ai", category_id=food)
    await session.flush()
    now = datetime.now(timezone.utc)
    e1 = _event(session, t1, source="ai", new=food, confidence=95, created_at=now)
    _event(session, t2, source="memory", new=food, confidence=None, created_at=now - timedelta(minutes=1))
    _event(session, t3, source="user", new=food)
    undone = _event(session, t4, source="ai", new=food)
    undone.undone_at = now
    await session.commit()

    resp = await client.get(f"{API}/applied", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    items = body["items"]
    assert [i["source"] for i in items] == ["ai", "memory"]
    first = items[0]
    assert first["event_id"] == str(e1.id)
    assert first["transaction"]["id"] == str(t1.id)
    assert first["transaction"]["description"] == "A"
    assert Decimal(str(first["transaction"]["amount"])) == Decimal("12.00")
    assert first["transaction"]["currency"] == "BRL"
    assert first["new_category_id"] == str(food)
    assert first["confidence"] == 95
    assert first["reason"] == "mercado"

    only_ai = (await client.get(f"{API}/applied?source=ai&min_confidence=90", headers=auth_headers)).json()
    assert [i["event_id"] for i in only_ai["items"]] == [str(e1.id)]
    paged = (await client.get(f"{API}/applied?limit=1&offset=1", headers=auth_headers)).json()
    assert [i["source"] for i in paged["items"]] == ["memory"]
    assert (await client.get(f"{API}/applied?limit=500", headers=auth_headers)).status_code == 422


@pytest.mark.asyncio
async def test_undo_run_endpoint(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_account.workspace_id, kind="classify",
        trigger="ingest", status="done", stats={}, detail={},
    )
    session.add(run)
    t1 = _tx(session, test_user, test_account, "A", source="ai", category_id=food)
    t2 = _tx(session, test_user, test_account, "B", source="ai", category_id=food)
    await session.flush()
    _event(session, t1, new=food, run_id=run.id)
    _event(session, t2, new=food, run_id=run.id)
    await session.commit()

    resp = await client.post(f"{API}/undo", headers=auth_headers, json={"run_id": str(run.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"restored": 2, "skipped": 0}
    for tx in (t1, t2):
        fresh = await _fresh(session, Transaction, tx.id)
        assert fresh.category_id is None
        assert fresh.category_source == "none"


@pytest.mark.asyncio
async def test_provenance(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    tx = _tx(session, test_user, test_account, "Padaria", source="ai", category_id=food,
             original_description="PADARIA DO ZE 123")
    await session.flush()
    now = datetime.now(timezone.utc)
    for i in range(6):
        _event(session, tx, new=food, created_at=now - timedelta(minutes=10 - i), reason=f"r{i}")
    await session.commit()

    resp = await client.get(f"{API}/transactions/{tx.id}/provenance", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_source"] == "ai"
    assert body["original_description"] == "PADARIA DO ZE 123"
    assert [e["reason"] for e in body["events"]] == ["r5", "r4", "r3", "r2", "r1"]
    assert (await client.get(f"{API}/transactions/{uuid.uuid4()}/provenance", headers=auth_headers)).status_code == 404


# --- similar / rule draft -----------------------------------------------------


@pytest.mark.asyncio
async def test_similar_excludes_protected(client, auth_headers, session, test_user, test_account, test_categories):
    food, transport = test_categories[0].id, test_categories[1].id
    ref = _tx(session, test_user, test_account, "PG *IFOOD 111", source="user", category_id=food)
    open1 = _tx(session, test_user, test_account, "IFOOD 222", source="provider", category_id=transport)
    open2 = _tx(session, test_user, test_account, "IFOOD 333", source="none")
    protected = _tx(session, test_user, test_account, "IFOOD 444", source="user", category_id=transport)
    legacy = _tx(session, test_user, test_account, "IFOOD 555", source="legacy", category_id=transport)
    same = _tx(session, test_user, test_account, "IFOOD 666", source="ai", category_id=food)
    _tx(session, test_user, test_account, "UBER 777", source="none")
    await session.commit()

    resp = await client.post(f"{API}/similar", headers=auth_headers, json={"transaction_id": str(ref.id)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merchant_key"] == "IFOOD"
    assert body["count"] == 2
    assert sorted(body["transaction_ids"]) == sorted([str(open1.id), str(open2.id)])

    applied = await client.post(f"{API}/similar/apply", headers=auth_headers, json={"transaction_id": str(ref.id)})
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 2
    for tx in (open1, open2):
        fresh = await _fresh(session, Transaction, tx.id)
        assert fresh.category_id == food
        assert fresh.category_source == "user"
    assert (await _fresh(session, Transaction, protected.id)).category_id == transport
    assert (await _fresh(session, Transaction, legacy.id)).category_id == transport
    assert (await _fresh(session, Transaction, same.id)).category_source == "ai"
    # One decision about the store, learned once (not once per row).
    memory = (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == "IFOOD")
    )).scalar_one_or_none()
    assert memory is not None
    assert memory.counts == {str(food): 1}


@pytest.mark.asyncio
async def test_similar_has_no_store_without_merchant_identity(client, auth_headers, session, test_user, test_account, test_categories):
    ref = _tx(session, test_user, test_account, "NULL", source="user", category_id=test_categories[0].id)
    _tx(session, test_user, test_account, "NULL", source="none")
    await session.commit()
    resp = await client.post(f"{API}/similar", headers=auth_headers, json={"transaction_id": str(ref.id)})
    assert resp.json() == {"merchant_key": "UNKNOWN", "count": 0, "transaction_ids": [], "transactions": []}
    apply = await client.post(f"{API}/similar/apply", headers=auth_headers, json={"transaction_id": str(ref.id)})
    assert apply.status_code == 422
    assert "padrão seguro" in apply.json()["detail"]


@pytest.mark.asyncio
async def test_rule_draft_uses_merchant_key(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    tx = _tx(session, test_user, test_account, "PG *IFOOD 12345 SAO PAULO", source="user", category_id=food)
    await session.commit()
    resp = await client.get(f"{API}/rule-draft/{tx.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conditions"] == [{"field": "description", "op": "contains", "value": "IFOOD"}]
    assert body["actions"] == [{"op": "set_category", "value": str(food)}]
    assert body["name"]


@pytest.mark.asyncio
async def test_rule_draft_strips_person_prefix(client, auth_headers, session, test_user, test_account, test_categories):
    tx = _tx(session, test_user, test_account, "Transferencia enviada|MARIA SILVA", category_id=test_categories[0].id)
    await session.commit()
    body = (await client.get(f"{API}/rule-draft/{tx.id}", headers=auth_headers)).json()
    assert body["conditions"] == [{"field": "description", "op": "contains", "value": "MARIA SILVA"}]


@pytest.mark.asyncio
async def test_rule_draft_never_uses_hashed_person_key(client, auth_headers, session, test_user, test_account, test_categories):
    plain = _tx(session, test_user, test_account, "Transferencia enviada|123.456.789-09", category_id=test_categories[0].id)
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_account.workspace_id,
                  name="123.456.789-09", source="sync")
    session.add(payee)
    await session.flush()
    with_payee = _tx(session, test_user, test_account, "Transferencia enviada|123.456.789-09",
                     category_id=test_categories[0].id, payee_id=payee.id)
    await session.commit()

    resp = await client.get(f"{API}/rule-draft/{plain.id}", headers=auth_headers)
    assert resp.status_code == 422
    assert "padrão seguro" in resp.json()["detail"]

    body = (await client.get(f"{API}/rule-draft/{with_payee.id}", headers=auth_headers)).json()
    assert body["conditions"] == [{"field": "payee_id", "op": "equals", "value": str(payee.id)}]
    assert "123" not in body["name"]


@pytest.mark.asyncio
async def test_rule_draft_person_name_not_in_description(client, auth_headers, session, test_user, test_account, test_categories):
    plain = _tx(session, test_user, test_account, "Transferencia enviada|MARIA SILVA-COSTA", category_id=test_categories[0].id)
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_account.workspace_id,
                  name="Maria", source="description")
    session.add(payee)
    await session.flush()
    with_payee = _tx(session, test_user, test_account, "Transferencia enviada|MARIA SILVA-COSTA",
                     category_id=test_categories[0].id, payee_id=payee.id)
    await session.commit()

    assert (await client.get(f"{API}/rule-draft/{plain.id}", headers=auth_headers)).status_code == 422
    body = (await client.get(f"{API}/rule-draft/{with_payee.id}", headers=auth_headers)).json()
    assert body["conditions"] == [{"field": "payee_id", "op": "equals", "value": str(payee.id)}]


@pytest.mark.asyncio
async def test_rule_draft_payee_key_not_in_description_uses_payee(client, auth_headers, session, test_user, test_account, test_categories):
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_account.workspace_id,
                  name="Amazon Servicos de Varejo LTDA", source="sync")
    session.add(payee)
    await session.flush()
    tx = _tx(session, test_user, test_account, "AMZ PSJ", category_id=test_categories[0].id, payee_id=payee.id)
    await session.commit()
    body = (await client.get(f"{API}/rule-draft/{tx.id}", headers=auth_headers)).json()
    assert body["conditions"] == [{"field": "payee_id", "op": "equals", "value": str(payee.id)}]


# --- settings / run -----------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_get_and_put(client, auth_headers, session, test_workspace):
    resp = await client.get(f"{API}/settings", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_apply_threshold"] == 90
    assert resp.json()["enabled"] is True

    put = await client.put(f"{API}/settings", headers=auth_headers, json={"auto_apply_threshold": 95, "monthly_cap_usd": "2.50"})
    assert put.status_code == 200, put.text
    assert put.json()["auto_apply_threshold"] == 95
    row = (await session.execute(
        select(CategorizationSettings).where(CategorizationSettings.workspace_id == test_workspace.id)
    )).scalar_one()
    await session.refresh(row)
    assert row.auto_apply_threshold == 95
    assert row.monthly_cap_usd == Decimal("2.50")

    bad = await client.put(f"{API}/settings", headers=auth_headers, json={"llm_connection_id": str(uuid.uuid4())})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_requires_manager_role(client, session, test_workspace):
    editor = await _member(session, client, test_workspace, "editor", "editor-role@example.com")
    assert (await client.get(f"{API}/settings", headers=editor)).status_code == 200
    resp = await client.put(f"{API}/settings", headers=editor, json={"auto_apply_threshold": 95})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_run_creates_queued_manual_run_and_enqueues(client, auth_headers, session, test_workspace, categorization_enqueue):
    resp = await client.post(f"{API}/run", headers=auth_headers, json={"scope": "backlog"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    run = await _fresh(session, CategorizationRun, uuid.UUID(body["id"]))
    assert (run.kind, run.trigger, run.status) == ("classify", "manual", "queued")
    categorization_enqueue.assert_called_once_with(test_workspace.id, "manual", run_id=run.id)


@pytest.mark.asyncio
async def test_run_returns_busy_run(client, auth_headers, session, test_workspace, categorization_enqueue):
    busy = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify",
        trigger="ingest", status="running", stats={}, detail={},
    )
    session.add(busy)
    await session.commit()
    resp = await client.post(f"{API}/run", headers=auth_headers, json={"scope": "backlog"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(busy.id)
    categorization_enqueue.assert_not_called()
    runs = (await session.execute(select(CategorizationRun))).scalars().all()
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_run_marks_failed_when_enqueue_fails(client, auth_headers, session, categorization_enqueue):
    categorization_enqueue.return_value = False
    resp = await client.post(f"{API}/run", headers=auth_headers, json={"scope": "backlog"})
    assert resp.status_code == 503
    run = (await session.execute(select(CategorizationRun))).scalar_one()
    await session.refresh(run)
    assert run.status == "failed"
    assert run.error == "enqueue_failed"
    assert run.finished_at is not None


# --- permissions ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("post", "/review/accept", {"merchant_key": "X", "category_id": str(uuid.uuid4())}),
    ("post", "/review/dismiss", {"merchant_key": "X"}),
    ("post", "/review/create-category", {"group": "G", "name": "N", "merchant_keys": []}),
    ("post", "/undo", {"event_ids": []}),
    ("post", "/similar", {"transaction_id": str(uuid.uuid4())}),
    ("post", "/similar/apply", {"transaction_id": str(uuid.uuid4())}),
    ("put", "/settings", {"enabled": False}),
    ("post", "/run", {"scope": "backlog"}),
])
async def test_viewer_cannot_write(client, viewer_auth_headers, method, path, body):
    resp = await client.request(method.upper(), f"{API}{path}", headers=viewer_auth_headers, json=body)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read(client, viewer_auth_headers):
    for path in ("/summary", "/review", "/applied", "/settings"):
        assert (await client.get(f"{API}{path}", headers=viewer_auth_headers)).status_code == 200


# --- transactions list ------------------------------------------------------------


@pytest.mark.asyncio
async def test_transactions_filter_by_category_source(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    ai = _tx(session, test_user, test_account, "A", source="ai", category_id=food)
    mem = _tx(session, test_user, test_account, "B", source="memory", category_id=food)
    _tx(session, test_user, test_account, "C", source="user", category_id=food)
    await session.commit()

    resp = await client.get("/api/transactions?category_source=ai,memory&limit=50", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert sorted(i["id"] for i in items) == sorted([str(ai.id), str(mem.id)])
    assert {i["category_source"] for i in items} == {"ai", "memory"}

    one = (await client.get("/api/transactions?category_source=user", headers=auth_headers)).json()["items"]
    assert [i["category_source"] for i in one] == ["user"]
    bad = await client.get("/api/transactions?category_source=bogus", headers=auth_headers)
    assert bad.status_code == 422



@pytest.mark.asyncio
async def test_transactions_flag_pending_categorization(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    waiting = _tx(session, test_user, test_account, "A")
    resolved = _tx(session, test_user, test_account, "B")
    plain = _tx(session, test_user, test_account, "C", source="ai", category_id=food)
    await session.flush()
    _suggest(session, waiting, "A", category_id=food)
    done = _suggest(session, resolved, "B", category_id=food)
    done.status = "dismissed"
    await session.commit()

    items = (await client.get("/api/transactions?limit=50", headers=auth_headers)).json()["items"]
    flags = {i["id"]: i["categorization_pending"] for i in items}
    assert flags[str(waiting.id)] is True
    assert flags[str(resolved.id)] is False
    assert flags[str(plain.id)] is False


@pytest.mark.asyncio
async def test_calendar_items_carry_origin_and_pending_review(client, auth_headers, session, test_user, test_account, test_categories):
    """T14-1: the calendar shows the same origin seal as the lists."""
    food = test_categories[0].id
    filed = _tx(session, test_user, test_account, "IFOOD", source="ai", category_id=food, day=date(2026, 9, 10))
    waiting = _tx(session, test_user, test_account, "PADARIA", day=date(2026, 9, 11))
    mine = _tx(session, test_user, test_account, "POSTO", source="user", category_id=food, day=date(2026, 9, 12))
    # A bank row still pending is a projected item, but a real transaction.
    held = _tx(session, test_user, test_account, "MERCADO", source="ai", category_id=food,
               day=date(2026, 9, 13), status="pending")
    await session.flush()
    _suggest(session, waiting, "PADARIA", category_id=food)
    await session.commit()

    resp = await client.get("/api/transactions/calendar?month=2026-09-01", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    items = {i["id"]: i for day in resp.json()["days"] for i in day["items"] if i["id"]}
    seal = {
        tx_id: (items[str(tx_id)]["category_source"], items[str(tx_id)]["categorization_pending"])
        for tx_id in (filed.id, waiting.id, mine.id, held.id)
    }
    assert seal == {
        filed.id: ("ai", False), waiting.id: ("none", True), mine.id: ("user", False),
        held.id: ("ai", False),
    }
    assert items[str(held.id)]["kind"] == "projected"


@pytest.mark.asyncio
async def test_applied_exposes_store_key_and_name(client, auth_headers, session, test_user, test_account, test_categories):
    food = test_categories[0].id
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_account.workspace_id,
                  name="Padaria Estrela", source="sync")
    session.add(payee)
    await session.flush()
    now = datetime.now(timezone.utc)
    with_payee = _tx(session, test_user, test_account, "COMPRA NO DEBITO PADARIA ESTRELA", category_id=food,
                     source="rule", payee_id=payee.id)
    other = _tx(session, test_user, test_account, "COMPRA NO DEBITO MERCADO SOL", category_id=food, source="rule")
    hashed = _tx(session, test_user, test_account, "Transferencia enviada|123.456.789-09", category_id=food, source="rule")
    await session.flush()
    _event(session, with_payee, source="rule", new=food, created_at=now)
    _event(session, other, source="rule", new=food, created_at=now - timedelta(minutes=1))
    _event(session, hashed, source="rule", new=food, created_at=now - timedelta(minutes=2))
    await session.commit()

    items = (await client.get(f"{API}/applied", headers=auth_headers)).json()["items"]
    by_desc = {i["transaction"]["description"]: i for i in items}
    padaria = by_desc["COMPRA NO DEBITO PADARIA ESTRELA"]
    mercado = by_desc["COMPRA NO DEBITO MERCADO SOL"]
    assert padaria["store_name"] == "Padaria Estrela"
    assert padaria["merchant_key"].startswith("P:")
    assert mercado["merchant_key"] != padaria["merchant_key"]
    assert mercado["store_name"] == "Mercado Sol"
    pix = by_desc["Transferencia enviada|123.456.789-09"]
    assert pix["merchant_key"].startswith("PIX:#")
    assert "123" not in pix["store_name"] and "789" not in pix["store_name"]


@pytest.mark.asyncio
async def test_summary_keeps_the_settings_row_it_creates(postgres_sessions):
    """The first summary creates the settings row: the route commits it, so
    a poll is not an INSERT rolled back every time (Postgres: on SQLite the
    savepoint release alone commits)."""
    from types import SimpleNamespace
    from typing import Any, cast

    from app.api import categorization as categorization_api
    from app.models.account import Account
    from tests.test_admin_service import _make_user

    async with postgres_sessions() as session:
        user = await _make_user(session, f"sum_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.commit()
        ws = account.workspace_id
    ctx = cast(Any, SimpleNamespace(workspace=SimpleNamespace(id=ws)))

    for _ in range(2):
        async with postgres_sessions() as session:
            await categorization_api.get_summary(ctx=ctx, session=session)

    async with postgres_sessions() as session:
        rows = (await session.execute(
            select(CategorizationSettings).where(CategorizationSettings.workspace_id == ws)
        )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_summary_counts_stores_like_the_review_queue(client, auth_headers, session, test_user, test_account):
    """A row with two pending suggestions under different store keys counts
    once, under its latest one, like the Review tab groups it."""
    tx = _tx(session, test_user, test_account, "PADARIA DO ZE")
    other = _tx(session, test_user, test_account, "POSTO SHELL")
    await session.flush()
    old = _suggest(session, tx, "PADARIA DO ZE")
    old.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    _suggest(session, tx, "P:PADARIA DO ZE")
    _suggest(session, other, "POSTO SHELL")
    await session.commit()

    queue = (await client.get(f"{API}/review", headers=auth_headers)).json()
    body = (await client.get(f"{API}/summary", headers=auth_headers)).json()

    assert queue["total"] == 2
    assert sorted(g["merchant_key"] for g in queue["groups"]) == ["P:PADARIA DO ZE", "POSTO SHELL"]
    assert body["pending_groups"] == queue["total"]
    assert body["pending_transactions"] == 2


@pytest.mark.asyncio
async def test_create_category_rolls_back_with_the_rest(session, test_user, test_workspace, monkeypatch):
    """The new category and group belong to the caller's transaction: a
    failure while filing the stores takes them back too."""
    from app.services.categorization import review

    async def boom(*_args, **_kwargs):
        raise RuntimeError("filing failed")

    ws_id, user_id = test_workspace.id, test_user.id
    monkeypatch.setattr(review, "_accept_keys", boom)
    with pytest.raises(RuntimeError):
        await review.create_category(
            session, ws_id, user_id,
            group="Pets Novos", name="Veterinário", merchant_keys=["VET1"],
        )
    await session.rollback()

    assert (await session.execute(
        select(Category.id).where(Category.workspace_id == ws_id, Category.name == "Veterinário")
    )).scalars().all() == []
    assert (await session.execute(
        select(CategoryGroup.id).where(CategoryGroup.workspace_id == ws_id, CategoryGroup.name == "Pets Novos")
    )).scalars().all() == []


@pytest.mark.asyncio
async def test_undo_of_a_big_batch_takes_a_fixed_number_of_queries(
    session, test_user, test_account, test_workspace, test_categories
):
    """"Desfazer este lote" loads what it needs per chunk, not per event:
    the statements it runs do not grow with the batch."""
    from sqlalchemy import event as sa_event

    from app.services.categorization import review

    async def batch(size):
        run = CategorizationRun(
            id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify",
            trigger="manual", status="done", stats={}, detail={},
        )
        session.add(run)
        for n in range(size):
            payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                          name=f"Loja {size} {n}", source="ai")
            session.add(payee)
            tx = _tx(session, test_user, test_account, f"LOJA {size} {n}", source="ai",
                     category_id=test_categories[0].id, payee_id=payee.id)
            await session.flush()
            ev = _event(session, tx, new=test_categories[0].id, run_id=run.id)
            ev.new_payee_id = payee.id
            ev.previous_payee_id = payee.id
        await session.commit()
        session.expunge_all()
        statements = []

        def count(*_args, **_kwargs):
            statements.append(1)

        engine = session.bind.sync_engine
        sa_event.listen(engine, "before_cursor_execute", count)
        try:
            result = await review.undo(session, test_workspace.id, run_id=run.id, user_id=test_user.id)
        finally:
            sa_event.remove(engine, "before_cursor_execute", count)
        await session.commit()
        assert result.restored == size
        return len(statements)

    small, big = await batch(3), await batch(40)
    assert big == small


@pytest.mark.asyncio
async def test_summary_and_applied_leave_confirmations_out(
    client, auth_headers, session, test_user, test_account, test_categories
):
    """A confirmation (the pipeline settling a guess at the category it
    already held) changed nothing the person would check or undo."""
    food, transport = test_categories[0].id, test_categories[1].id
    filed = _tx(session, test_user, test_account, "A", source="ai", category_id=food)
    moved = _tx(session, test_user, test_account, "B", source="memory", category_id=food)
    confirmed = _tx(session, test_user, test_account, "C", source="ai", category_id=food)
    await session.flush()
    now = datetime.now(timezone.utc)
    real = _event(session, filed, new=food, created_at=now)
    other = _event(session, moved, source="memory", previous=transport, new=food,
                   created_at=now - timedelta(minutes=1))
    confirmation = _event(session, confirmed, previous=food, new=food)
    confirmation.previous_source = "provider"
    await session.commit()

    summary = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    assert summary["applied_last_7d"] == 2
    body = (await client.get(f"{API}/applied", headers=auth_headers)).json()
    assert body["total"] == 2
    assert [i["event_id"] for i in body["items"]] == [str(real.id), str(other.id)]


@pytest.mark.asyncio
async def test_undo_run_leaves_its_confirmations_standing(
    client, auth_headers, session, test_user, test_account, test_categories
):
    """"Desfazer este lote" takes back what the run changed; a guess it only
    confirmed stays settled and does not go back to Revisar."""
    food = test_categories[0].id
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_account.workspace_id, kind="classify",
        trigger="manual", status="done", stats={}, detail={},
    )
    session.add(run)
    changed = _tx(session, test_user, test_account, "LOJA A", source="ai", category_id=food)
    confirmed = _tx(session, test_user, test_account, "LOJA B", source="ai", category_id=food)
    await session.flush()
    _event(session, changed, new=food, run_id=run.id)
    confirmation = _event(session, confirmed, previous=food, new=food, run_id=run.id)
    confirmation.previous_source = "provider"
    await session.commit()

    resp = await client.post(f"{API}/undo", headers=auth_headers, json={"run_id": str(run.id)})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"restored": 1, "skipped": 0}
    fresh = await _fresh(session, Transaction, changed.id)
    assert (fresh.category_id, fresh.category_source) == (None, "none")
    fresh = await _fresh(session, Transaction, confirmed.id)
    assert (fresh.category_id, fresh.category_source) == (food, "ai")
    assert (await _fresh(session, CategorizationEvent, confirmation.id)).undone_at is None
    pending = (await session.execute(
        select(CategorizationSuggestion.transaction_id).where(
            CategorizationSuggestion.status == "pending"
        )
    )).scalars().all()
    assert pending == [changed.id]

    # Undoing the confirmation itself, by id, still works as before.
    resp = await client.post(
        f"{API}/undo", headers=auth_headers, json={"event_ids": [str(confirmation.id)]}
    )
    assert resp.json() == {"restored": 1, "skipped": 0}
    fresh = await _fresh(session, Transaction, confirmed.id)
    assert (fresh.category_id, fresh.category_source) == (food, "provider")
