"""One choice for a store: an existing category or one created on the spot."""
import uuid

import pytest
from sqlalchemy import func, select

from app.models.categorization import CategorizationEvent, CategorizationSuggestion, MerchantMemory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.schemas.category import CategoryCreate
from app.schemas.category_group import CategoryGroupCreate
from app.services import category_group_service, category_service
from app.services.categorization.creation import suggest_group_style, suggest_style
from app.services.categorization.memory import learn_choice, remember_ai_decision
from app.services.categorization.merchant_key import merchant_key
from tests.test_categorization_api import API, _counts, _fresh, _suggest, _tx, _undo

async def _choose(client, headers, body, status=200):
    resp = await client.post(f"{API}/choose", headers=headers, json=body)
    assert resp.status_code == status, resp.text
    return resp.json()


async def _count(session, model, ws_id):
    return await session.scalar(
        select(func.count(model.id)).where(model.workspace_id == ws_id)
    )


async def _pending_store(session, user, account, description, key, n=2, **sug):
    txs = [_tx(session, user, account, description) for _ in range(n)]
    await session.flush()
    for tx in txs:
        _suggest(session, tx, key, **sug)
    await session.commit()
    return txs


async def _memory(session, key):
    row = (await session.execute(
        select(MerchantMemory).where(MerchantMemory.merchant_key == key)
    )).scalar_one_or_none()
    if row is not None:
        await session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_choose_creates_group_and_category_and_applies_pending(
    client, auth_headers, session, test_user, test_account, test_workspace
):
    assert (await session.execute(
        select(CategoryGroup).where(
            CategoryGroup.workspace_id == test_workspace.id, CategoryGroup.name == "Transporte"
        )
    )).scalar_one_or_none() is None
    txs = await _pending_store(session, test_user, test_account, "LOCADORA X", "LOCADORA")

    body = await _choose(client, auth_headers, {
        "merchant_key": "LOCADORA", "new_category": {"group": "Transporte", "name": "Carro"},
    })

    assert body["created_group"] is True
    assert body["created_category"] is True
    assert body["applied"] == 2
    assert len(body["event_ids"]) == 2
    cat_id = uuid.UUID(body["category_id"])
    category = await _fresh(session, Category, cat_id)
    assert (category.name, category.icon) == ("Carro", "car")
    group = await _fresh(session, CategoryGroup, category.group_id)
    assert group.name == "Transporte"
    assert group.workspace_id == test_workspace.id
    for tx in txs:
        fresh = await _fresh(session, Transaction, tx.id)
        assert (fresh.category_id, fresh.category_source) == (cat_id, "user")
    assert await _counts(session, "LOCADORA") == {str(cat_id): 1}
    picks = await session.scalar(
        select(func.count(CategorizationEvent.id)).where(
            CategorizationEvent.id.in_([uuid.UUID(e) for e in body["event_ids"]]),
            CategorizationEvent.memory_pick.is_(True),
        )
    )
    assert picks == 1


@pytest.mark.asyncio
async def test_choose_reuses_existing_names_ignoring_accents(
    client, auth_headers, session, test_user, test_account, test_workspace
):
    group = await category_group_service.create_group(
        session, test_workspace.id, test_user.id, CategoryGroupCreate(name="Saúde")
    )
    category = await category_service.create_category(
        session, test_workspace.id, test_user.id,
        CategoryCreate(name="Farmácia", group_id=group.id),
    )
    groups_before = await _count(session, CategoryGroup, test_workspace.id)
    cats_before = await _count(session, Category, test_workspace.id)
    await _pending_store(session, test_user, test_account, "DROGARIA Y", "DROGARIA", n=1)

    body = await _choose(client, auth_headers, {
        "merchant_key": "DROGARIA", "new_category": {"group": "saude", "name": "FARMACIA"},
    })

    assert body["created_group"] is False
    assert body["created_category"] is False
    assert body["category_id"] == str(category.id)
    assert body["applied"] == 1
    assert await _count(session, CategoryGroup, test_workspace.id) == groups_before
    assert await _count(session, Category, test_workspace.id) == cats_before


@pytest.mark.asyncio
async def test_choose_new_group_with_same_named_category(
    client, auth_headers, session, test_user, test_account, test_workspace
):
    await _pending_store(session, test_user, test_account, "NIMBLOO SA", "NIMBLOO", n=1)

    body = await _choose(client, auth_headers, {
        "merchant_key": "NIMBLOO", "new_category": {"group": "Nimbloo", "name": "Nimbloo"},
    })

    assert (body["created_group"], body["created_category"]) == (True, True)
    category = await _fresh(session, Category, uuid.UUID(body["category_id"]))
    group = await _fresh(session, CategoryGroup, category.group_id)
    assert (group.name, category.name) == ("Nimbloo", "Nimbloo")
    assert len((await session.execute(
        select(CategoryGroup.id).where(
            CategoryGroup.workspace_id == test_workspace.id, CategoryGroup.name == "Nimbloo"
        )
    )).scalars().all()) == 1


@pytest.mark.asyncio
async def test_choose_hidden_group_is_not_reused(
    client, auth_headers, session, test_user, test_account, test_workspace
):
    hidden = await category_group_service.create_group(
        session, test_workspace.id, test_user.id, CategoryGroupCreate(name="Pets")
    )
    hidden.is_hidden = True
    await session.commit()
    await _pending_store(session, test_user, test_account, "PETZ", "PETZ", n=1)

    body = await _choose(client, auth_headers, {
        "merchant_key": "PETZ", "new_category": {"group": "pets", "name": "Ração"},
    })

    assert body["created_group"] is True
    category = await _fresh(session, Category, uuid.UUID(body["category_id"]))
    assert category.group_id != hidden.id
    assert body["applied"] == 1


@pytest.mark.asyncio
async def test_choose_pending_skips_rows_categorized_by_hand_since(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0].id, test_categories[1].id
    a = _tx(session, test_user, test_account, "PADARIA")
    manual = _tx(session, test_user, test_account, "PADARIA", source="user", category_id=transport)
    await session.flush()
    _suggest(session, a, "PADARIA", category_id=food)
    sm = _suggest(session, manual, "PADARIA", category_id=food)
    await session.commit()

    body = await _choose(client, auth_headers, {"merchant_key": "PADARIA", "category_id": str(food)})

    assert body["applied"] == 1
    assert (body["created_group"], body["created_category"]) == (False, False)
    fresh = await _fresh(session, Transaction, manual.id)
    assert (fresh.category_id, fresh.category_source) == (transport, "user")
    assert (await _fresh(session, Transaction, a.id)).category_id == food
    assert (await _fresh(session, CategorizationSuggestion, sm.id)).status == "accepted"


@pytest.mark.asyncio
async def test_choose_pending_without_rows_is_404(
    client, auth_headers, session, test_workspace, test_categories
):
    groups_before = await _count(session, CategoryGroup, test_workspace.id)
    await _choose(client, auth_headers, {
        "merchant_key": "SUMIU", "category_id": str(test_categories[0].id),
    }, status=404)
    await _choose(client, auth_headers, {
        "merchant_key": "SUMIU", "new_category": {"group": "Novo", "name": "Nada"},
    }, status=404)
    assert await _count(session, CategoryGroup, test_workspace.id) == groups_before


@pytest.mark.asyncio
async def test_choose_pending_all_categorized_by_hand_since_is_404(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0].id, test_categories[1].id
    manual = _tx(session, test_user, test_account, "PADARIA", source="user", category_id=transport)
    await session.flush()
    _suggest(session, manual, "PADARIA", category_id=food)
    await session.commit()

    await _choose(client, auth_headers, {"merchant_key": "PADARIA", "category_id": str(food)}, status=404)
    fresh = await _fresh(session, Transaction, manual.id)
    assert (fresh.category_id, fresh.category_source) == (transport, "user")
    assert await _counts(session, "PADARIA") is None


@pytest.mark.asyncio
async def test_choose_with_transaction_ids_applies_to_those_rows_and_counts_one_pick(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport, income = (c.id for c in test_categories)
    key = merchant_key("PADARIA PAO QUENTE")
    await learn_choice(session, test_account.workspace_id, key, food)
    ai_row = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="ai", category_id=transport)
    user_row = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="user", category_id=income)
    untouched = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="ai", category_id=transport)
    await session.flush()
    sug = _suggest(session, ai_row, key, category_id=transport)
    await session.commit()

    body = await _choose(client, auth_headers, {
        "merchant_key": key, "category_id": str(food),
        "transaction_ids": [str(ai_row.id), str(user_row.id)],
    })

    assert body["applied"] == 2
    for tx in (ai_row, user_row):
        fresh = await _fresh(session, Transaction, tx.id)
        assert (fresh.category_id, fresh.category_source) == (food, "user")
    assert (await _fresh(session, Transaction, untouched.id)).category_id == transport
    assert (await _fresh(session, CategorizationSuggestion, sug.id)).status == "accepted"
    assert await _counts(session, key) == {str(food): 2}

    assert await _undo(client, auth_headers, body["event_ids"]) == {"restored": 2, "skipped": 0}
    assert await _counts(session, key) == {str(food): 1}
    fresh = await _fresh(session, Transaction, user_row.id)
    assert (fresh.category_id, fresh.category_source) == (income, "user")


@pytest.mark.asyncio
async def test_choose_with_ids_learns_under_the_suggestions_key_and_undo_returns_it(
    client, auth_headers, session, test_user, test_account, test_categories
):
    """A row pending under another key than the one computed now (its payee
    changed, or the key rule did) teaches and is undone under that key."""
    food, transport = test_categories[0].id, test_categories[1].id
    computed = merchant_key("PADARIA PAO QUENTE")
    row = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="ai", category_id=transport)
    await session.flush()
    _suggest(session, row, "PADARIA ANTIGA", category_id=transport)
    await session.commit()
    assert computed != "PADARIA ANTIGA"

    body = await _choose(client, auth_headers, {
        "merchant_key": "PADARIA ANTIGA", "category_id": str(food), "transaction_ids": [str(row.id)],
    })

    assert await _counts(session, "PADARIA ANTIGA") == {str(food): 1}
    assert await _counts(session, computed) is None
    assert await _undo(client, auth_headers, body["event_ids"]) == {"restored": 1, "skipped": 0}
    assert await _counts(session, "PADARIA ANTIGA") == {}
    assert await _counts(session, computed) is None


@pytest.mark.asyncio
async def test_choose_with_ids_counts_one_pick_per_store_legacy_rows_included(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0].id, test_categories[1].id
    padaria, mercado = merchant_key("PADARIA PAO QUENTE"), merchant_key("MERCADO BOM PRECO")
    rows = [
        _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="ai", category_id=transport),
        _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="legacy", category_id=transport),
        _tx(session, test_user, test_account, "MERCADO BOM PRECO", source="none"),
    ]
    await session.commit()

    body = await _choose(client, auth_headers, {
        "merchant_key": padaria, "category_id": str(food),
        "transaction_ids": [str(tx.id) for tx in rows],
    })

    assert body["applied"] == 3
    for tx in rows:
        fresh = await _fresh(session, Transaction, tx.id)
        assert (fresh.category_id, fresh.category_source) == (food, "user")
    assert await _counts(session, padaria) == {str(food): 1}
    assert await _counts(session, mercado) == {str(food): 1}
    picks = await session.scalar(
        select(func.count(CategorizationEvent.id)).where(
            CategorizationEvent.id.in_([uuid.UUID(e) for e in body["event_ids"]]),
            CategorizationEvent.memory_pick.is_(True),
        )
    )
    assert picks == 2


async def _ai_cache(session, ws_id, key, category_id):
    await remember_ai_decision(session, ws_id, key, category_id, 91, None)
    await session.commit()


@pytest.mark.asyncio
async def test_choose_reusing_names_keeps_other_stores_ai_cache_and_creating_drops_it(
    client, auth_headers, session, test_user, test_account, test_workspace, test_categories
):
    group = await category_group_service.create_group(
        session, test_workspace.id, test_user.id, CategoryGroupCreate(name="Saúde")
    )
    await category_service.create_category(
        session, test_workspace.id, test_user.id,
        CategoryCreate(name="Farmácia", group_id=group.id),
    )
    await _ai_cache(session, test_workspace.id, "ELSEWHERE", test_categories[0].id)
    await _pending_store(session, test_user, test_account, "DROGARIA Y", "DROGARIA", n=1)

    body = await _choose(client, auth_headers, {
        "merchant_key": "DROGARIA", "new_category": {"group": "saude", "name": "farmacia"},
    })
    assert body["created_category"] is False
    assert (await _memory(session, "ELSEWHERE")).ai_confidence == 91

    await _pending_store(session, test_user, test_account, "DROGARIA Z", "DROGARIA Z", n=1)
    body = await _choose(client, auth_headers, {
        "merchant_key": "DROGARIA Z", "new_category": {"group": "saude", "name": "Manipulação"},
    })
    assert body["created_category"] is True
    assert (await _memory(session, "ELSEWHERE")).ai_confidence is None


@pytest.mark.asyncio
async def test_choose_note_drops_the_ai_cache_of_the_noted_store(
    client, auth_headers, session, test_user, test_account, test_workspace, test_categories
):
    """The note's store is not one of the rows: only the note can have
    dropped its cached decision (a pick would drop it too)."""
    food, transport = test_categories[0].id, test_categories[1].id
    await _ai_cache(session, test_workspace.id, "AURINO", transport)
    row = _tx(session, test_user, test_account, "PADARIA PAO QUENTE")
    await session.commit()

    await _choose(client, auth_headers, {
        "merchant_key": "AURINO", "category_id": str(food),
        "transaction_ids": [str(row.id)], "note": "jardineiro",
    })

    memo = await _memory(session, "AURINO")
    assert (memo.note, memo.ai_category_id, memo.ai_confidence) == ("jardineiro", None, None)
    assert memo.counts == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"category_id": "CAT", "transaction_ids": []},
    {"new_category": {"group": "   ", "name": "Carro"}},
    {"new_category": {"group": "Transporte", "name": "  "}},
])
async def test_choose_rejects_empty_ids_and_blank_names(client, auth_headers, test_categories, extra):
    body = {"merchant_key": "PADARIA", **extra}
    if "category_id" in body:
        body["category_id"] = str(test_categories[0].id)
    resp = await client.post(f"{API}/choose", headers=auth_headers, json=body)
    assert resp.status_code == 422, resp.text
    # Refused by the schema, before the service.
    assert isinstance(resp.json()["detail"], list)


@pytest.mark.asyncio
async def test_choose_saves_note_and_forgets_ai_cache(
    client, auth_headers, session, test_user, test_account, test_categories
):
    food, transport = test_categories[0].id, test_categories[1].id
    await remember_ai_decision(session, test_account.workspace_id, "AURINO", transport, 90, "Aurino")
    await session.commit()
    await _pending_store(session, test_user, test_account, "PIX AURINO", "AURINO", n=1)

    await _choose(client, auth_headers, {
        "merchant_key": "AURINO", "category_id": str(food), "note": "Aurino: jardineiro semanal",
    })
    row = await _memory(session, "AURINO")
    assert row is not None
    assert row.note == "Aurino: jardineiro semanal"
    assert (row.ai_category_id, row.ai_confidence) == (None, None)
    assert row.counts == {str(food): 1}

    # Left out: the note stays; null: it goes.
    await _pending_store(session, test_user, test_account, "PIX AURINO", "AURINO", n=1)
    await _choose(client, auth_headers, {"merchant_key": "AURINO", "category_id": str(food)})
    assert (await _memory(session, "AURINO")).note == "Aurino: jardineiro semanal"
    await _pending_store(session, test_user, test_account, "PIX AURINO", "AURINO", n=1)
    await _choose(client, auth_headers, {"merchant_key": "AURINO", "category_id": str(food), "note": None})
    assert (await _memory(session, "AURINO")).note is None


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {},
    {"category_id": "CAT", "new_category": {"group": "Casa", "name": "Jardinagem"}},
])
async def test_choose_rejects_both_or_neither(client, auth_headers, test_categories, extra):
    body = {"merchant_key": "PADARIA", **extra}
    if "category_id" in body:
        body["category_id"] = str(test_categories[0].id)
    await _choose(client, auth_headers, body, status=422)


@pytest.mark.asyncio
async def test_choose_requires_write(client, viewer_auth_headers, test_categories):
    await _choose(client, viewer_auth_headers, {
        "merchant_key": "PADARIA", "category_id": str(test_categories[0].id),
    }, status=403)


@pytest.mark.asyncio
async def test_choose_unknown_rows_is_404(
    client, auth_headers, session, test_workspace, test_categories
):
    groups_before = await _count(session, CategoryGroup, test_workspace.id)
    await _choose(client, auth_headers, {
        "merchant_key": "PADARIA", "category_id": str(test_categories[0].id),
        "transaction_ids": [str(uuid.uuid4())],
    }, status=404)
    await _choose(client, auth_headers, {
        "merchant_key": "PADARIA", "new_category": {"group": "Novo", "name": "Nada"},
        "transaction_ids": [str(uuid.uuid4())],
    }, status=404)
    assert await _count(session, CategoryGroup, test_workspace.id) == groups_before


@pytest.mark.asyncio
async def test_choose_unknown_category_is_404(client, auth_headers, session, test_user, test_account):
    await _pending_store(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    await _choose(client, auth_headers, {
        "merchant_key": "PADARIA", "category_id": str(uuid.uuid4()),
    }, status=404)


@pytest.mark.parametrize("name,group,color,icon,expected_color", [
    ("Carro", "Transporte", None, "car", None),
    ("Jardinagem", "Casa", "#22c55e", "tree-pine", "#22c55e"),
    ("Xyz", "Abc", None, "circle-help", None),
    ("Uber", "Abc", "#6B7280", "bus", None),
    ("Xyz", "Saúde", None, "stethoscope", None),
    # A stem counts at the start of a word, not inside one.
    ("Carpete", "Abc", None, "circle-help", None),
    ("Representação", "Abc", None, "circle-help", None),
    ("Pet Shop", "Abc", None, "dog", None),
    ("Restaurantes", "Abc", None, "utensils-crossed", None),
    ("Águas", "Abc", None, "droplets", None),
    # Words that start like a stem but mean something else.
    ("Aguardando", "Abc", None, "circle-help", None),
    ("Petróleo", "Abc", None, "circle-help", None),
    ("Autônomo", "Abc", None, "circle-help", None),
    ("Casamento", "Abc", None, "circle-help", None),
])
def test_suggest_style(name, group, color, icon, expected_color):
    got_icon, got_color = suggest_style(name, group, color)
    assert got_icon == icon
    assert got_color.startswith("#")
    if expected_color is not None:
        assert got_color == expected_color
    # Deterministic: the same group always gets the same color.
    assert suggest_style(name, group, color) == (got_icon, got_color)


def test_suggest_group_style():
    assert suggest_group_style("Transporte")[0] == "bus"
    assert suggest_group_style("Abc")[0] == "circle-help"
    assert suggest_group_style("Abc")[1] == suggest_style("Xyz", "Abc", None)[1]


def test_every_icon_creation_can_emit_exists_in_the_frontend():
    """An icon the frontend does not know falls back to a generic one: every
    icon `creation` can store must be a key of `category-icons.ts`."""
    import re
    from pathlib import Path

    from app.services.categorization import creation

    icons = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "category-icons.ts"
    if not icons.is_file():
        pytest.skip("frontend icons not available outside a full checkout")
    source = icons.read_text(encoding="utf-8")
    known = set(re.findall(r"name:\s*'([^']+)'", source))
    emitted = {icon for _stems, icon in creation.STYLE_KEYWORDS}
    emitted |= {creation.DEFAULT_CATEGORY_ICON, creation.DEFAULT_GROUP_ICON}
    assert "circle-help" in known
    assert emitted - known == set()
