"""The category organizer: Opus proposes, the person approves op by op.

The SDK client is a mock (`beta.messages.create` / `messages.create`), so
the tests run the real input building, filtering and applying.
"""
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models.budget import Budget
from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSettings,
    MerchantMemory,
)
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services import category_service
from app.services.categorization import classifier, organizer
from app.services.categorization.organizer import (
    ORGANIZER_MODEL,
    ORGANIZER_SCHEMA,
    CategoryStat,
    OrganizerStats,
    StoreStat,
    apply_op,
    build_organizer_input,
    filter_ops,
    organizer_params,
)
from app.services.categorization.prompts import build_category_index
from app.services.categorization.settings import get_settings
from app.services.connection_service import _match_pluggy_category
from app.tasks.categorization_tasks import enqueue_organizer as real_enqueue_organizer

API = "/api/categorization"
_SHORT_RE = re.compile(r"^(c\d+) (.+)$", re.MULTILINE)


# --- helpers ------------------------------------------------------------------------


def _group(session, user, ws, name, position=0):
    group = CategoryGroup(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, name=name,
        icon="folder", color="#000000", position=position,
    )
    session.add(group)
    return group


def _cat(session, user, ws, name, group=None, **extra):
    cat = Category(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, name=name,
        icon="x", color="#000000", group_id=group.id if group is not None else None, **extra,
    )
    session.add(cat)
    return cat


def _tx(session, user, account, description, *, category=None, source="none",
        amount="50.00", days_ago=30, **extra):
    day = date.today() - timedelta(days=days_ago)
    tx = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=account.workspace_id,
        account_id=account.id, description=description, amount=Decimal(amount),
        currency="BRL", date=day, effective_date=day, type=extra.pop("type", "debit"),
        source="sync", status="posted",
        category_id=category.id if category is not None else None,
        category_source=source, created_at=datetime.now(timezone.utc), **extra,
    )
    session.add(tx)
    return tx


def _op(id_, op, *, target=None, group=None, name=None, children=(), into=None,
        why="motivo", tx=0, total=0):
    return {
        "id": id_, "op": op, "target": target, "group": group, "name": name,
        "children": list(children), "into": into, "why": why,
        "impact": {"tx": tx, "total": total},
    }


async def _organize_run(session, ws, ops, ids, stores=None):
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=ws.id, kind="organize", trigger="manual",
        status="done", stats={}, created_at=datetime.now(timezone.utc),
        detail={
            "ops": [{**op, "status": "pending", "labels": {}} for op in ops],
            "ids": {short: str(obj_id) for short, obj_id in ids.items()},
            "stores": stores or {},
        },
    )
    session.add(run)
    await session.commit()
    return run


async def _fresh(session, model, pk):
    return await session.get(model, pk, populate_existing=True)


def _message(payload, *, stop_reason="end_turn", model=ORGANIZER_MODEL):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=json.dumps(payload)),
        ],
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(
            input_tokens=1000, output_tokens=500,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _fake_client(*, beta=None, create=None):
    client = MagicMock()
    client.close = AsyncMock()
    client.beta.messages.create = AsyncMock(side_effect=beta)
    client.messages.create = AsyncMock(side_effect=create)
    return client


def _stats():
    """g1 Lazer (c1, c2), g2 Casa (c3..c8, full), stores i1..i3."""
    categories = {
        "c1": CategoryStat(name="Lazer", group="g1", tx=10, total=Decimal("400")),
        "c2": CategoryStat(name="Cinema", group="g1", tx=4, total=Decimal("120")),
    }
    for n in range(3, 9):
        categories[f"c{n}"] = CategoryStat(name=f"Casa {n}", group="g2", tx=1, total=Decimal("10"))
    return OrganizerStats(
        groups={"g1": "Lazer", "g2": "Casa"},
        categories=categories,
        stores={
            "i1": StoreStat(name="Steam", tx=2, total=Decimal("50")),
            "i2": StoreStat(name="Netflix", tx=5, total=Decimal("30")),
            "i3": StoreStat(name="Decolar", tx=1, total=Decimal("500")),
        },
    )


# --- params / schema ----------------------------------------------------------------


def test_organizer_params_opus_with_fallbacks():
    system = [{"type": "text", "text": "instruções"}]
    params = organizer_params(system, '{"groups":[]}')
    assert params["model"] == "claude-opus-5-5" == ORGANIZER_MODEL
    assert params["max_tokens"] == 16000
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["effort"] == "medium"
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": ORGANIZER_SCHEMA}
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["fallbacks"] == "default"
    assert params["system"] == system
    assert params["messages"] == [{"role": "user", "content": '{"groups":[]}'}]
    assert "temperature" not in params


def test_organizer_schema_is_strict():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for forbidden in ("minimum", "maximum", "minLength", "maxLength", "minItems"):
                assert forbidden not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ORGANIZER_SCHEMA)
    op = ORGANIZER_SCHEMA["properties"]["ops"]["items"]
    assert set(op["properties"]["op"]["enum"]) == {
        "create_group", "create_category", "rename", "move_category", "merge", "split_category",
    }


def test_sdk_accepts_fallbacks_on_beta_messages():
    """The installed SDK takes `fallbacks` and `betas` as real parameters."""
    import inspect

    from anthropic.resources.beta.messages.messages import AsyncMessages

    parameters = inspect.signature(AsyncMessages.create).parameters
    assert "fallbacks" in parameters
    assert "betas" in parameters


# --- filter -------------------------------------------------------------------------


def test_filter_ops_drops_tiny_categories_and_third_level():
    ops = [
        _op("o1", "create_category", group="g1", name="Games", children=["i1"]),
        _op("o2", "create_category", group="g1", name="Streaming", children=["i2"]),
        _op("o3", "create_category", group="g1", name="Viagem", children=["i3"]),
        _op("o4", "move_category", target="c1", group="c2"),
        _op("o5", "create_group", name="Sub", group="g1"),
        _op("o6", "move_category", target="c99", group="g1"),
        _op("o7", "move_category", target="c1", group="g2"),
        _op("o8", "split_category", target="g1", name="Lazer", children=["A", "B"]),
        _op("o9", "create_group", name="Vazio"),
        _op("o10", "rename", target="c2", name="Shows", tx=99, total=1),
        {"id": "o11", "op": "explode"},
        _op("o12", "merge", target="c2", into="c2"),
    ]
    kept = filter_ops(ops, _stats())
    assert [op["id"] for op in kept] == ["o2", "o3", "o10"]
    assert all(op["status"] == "pending" for op in kept)
    # Impact is recomputed from the real numbers, not the model's.
    assert kept[0]["impact"] == {"tx": 5, "total": 30.0}
    assert kept[1]["impact"] == {"tx": 1, "total": 500.0}
    assert kept[2]["impact"] == {"tx": 4, "total": 120.0}
    assert kept[0]["labels"]["children"] == ["Netflix"]


def test_filter_ops_group_sizes_and_split_threshold():
    stats = _stats()
    ops = [
        # A new group with a single category: both go.
        _op("o1", "create_group", name="Viagens"),
        _op("o2", "create_category", group="Viagens", name="Passagens", children=["i3"]),
        # A split into 2 needs 6 transactions or R$ 400: c1 has 10. Its group
        # name is the existing g1, so g1 keeps 3 categories.
        _op("o3", "split_category", target="c1", name="lazer", children=["Jogos", "Shows"]),
        # c2 has 4 transactions and R$ 120: too little for 2 children.
        _op("o4", "split_category", target="c2", name="Telas", children=["Cinema", "Teatro"]),
        # Seven children is outside 2-6.
        _op("o5", "split_category", target="c3", name="X", children=list("ABCDEFG")),
    ]
    kept = filter_ops(ops, stats)
    assert [op["id"] for op in kept] == ["o3"]


def test_filter_ops_merge_rules():
    stats = _stats()
    stats.categories["c9"] = CategoryStat(
        name="Transferências", group=None, tx=3, total=Decimal("900"), transfer=True
    )
    ops = [
        # Merging spending into a transfer category is refused.
        _op("o1", "merge", target="c2", into="c9"),
        # Leaves g1 with a single category: refused.
        _op("o2", "merge", target="c2", into="c1"),
        # Anything after a merge that names the merged category is refused.
        _op("o3", "merge", target="c4", into="c3"),
        _op("o4", "rename", target="c4", name="Outro nome"),
    ]
    kept = filter_ops(ops, stats)
    assert [op["id"] for op in kept] == ["o3"]
    assert kept[0]["impact"] == {"tx": 1, "total": 10.0}


# --- input / privacy ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_organizer_input_is_tree_and_store_aggregates_only(
    session, test_user, test_workspace, test_account
):
    lazer = _group(session, test_user, test_workspace, "Lazer")
    cinema = _cat(session, test_user, test_workspace, "Cinema", lazer)
    _cat(session, test_user, test_workspace, "Mercado")
    for _ in range(3):
        _tx(session, test_user, test_account, "CINEMARK SHOPPING 12345678 CPF 123.456.789-09",
            category=cinema, amount="40.00")
    _tx(session, test_user, test_account, "PIX ENVIADO 12345678909", amount="99.00")
    _tx(session, test_user, test_account, "MUITO ANTIGO", category=cinema, days_ago=400)
    await session.commit()

    text = await build_organizer_input(session, test_workspace.id)
    data = json.loads(text)
    assert data["groups"][0]["name"] == "Lazer"
    cat = data["groups"][0]["cats"][0]
    assert cat["name"] == "Cinema"
    assert (cat["q"], cat["v"]) == (3, 120)
    assert [c["name"] for c in data["ungrouped"]] == ["Mercado"]
    stores = {s["n"]: s for s in data["stores"]}
    cinemark = next(s for name, s in stores.items() if name.upper().startswith("CINEMARK"))
    assert cinemark["k"].startswith("i")
    assert (cinemark["q"], cinemark["v"], cinemark["c"]) == (3, 120, cat["c"])
    # No merchant keys, documents, raw descriptions or uuids leave the backend.
    assert "123.456.789-09" not in text
    assert "12345678909" not in text
    assert "12345678" not in text
    assert "PIX:" not in text
    assert "MUITO ANTIGO" not in text
    assert str(cinema.id) not in text


# --- the run ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_organize_run_stores_filtered_ops_and_fills_provider_map(
    session, test_user, test_workspace, test_account, monkeypatch
):
    lazer = _group(session, test_user, test_workspace, "Lazer")
    cinema = _cat(session, test_user, test_workspace, "Cinema", lazer)
    _cat(session, test_user, test_workspace, "Lazer", lazer)
    for _ in range(4):
        _tx(session, test_user, test_account, "NETFLIX.COM", category=cinema, amount="40.00")
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize",
        trigger="manual", status="running", stats={}, detail={},
    )
    session.add(run)
    await session.commit()

    seen = {}

    def answer(**params):
        seen.update(params)
        data = json.loads(params["messages"][0]["content"])
        store = data["stores"][0]["k"]
        cinema_short = data["groups"][0]["cats"][0]["c"]
        return _message({"ops": [
            _op("o1", "create_category", group="g1", name="Streaming", children=[store]),
            _op("o2", "rename", target=cinema_short, name="Cinema e teatro"),
            _op("o3", "move_category", target="c404", group="g1"),
        ]})

    client = _fake_client(beta=answer)
    monkeypatch.setattr(organizer, "resolve_client", AsyncMock(return_value=client))
    await organizer.organize(session, test_workspace.id, run)
    await session.commit()

    run = await _fresh(session, CategorizationRun, run.id)
    assert run.status == "done"
    assert run.model == ORGANIZER_MODEL
    assert run.cost_usd == Decimal("0.014000")
    assert [op["id"] for op in run.detail["ops"]] == ["o1", "o2"]
    assert seen["fallbacks"] == "default"
    assert seen["betas"] == ["server-side-fallback-2026-07-01"]
    client.messages.create.assert_not_called()
    settings = await _fresh(session, CategorizationSettings, (await get_settings(session, test_workspace.id)).id)
    # "Lazer" is a Pluggy target name; resolved by id after the first run.
    assert settings.provider_category_map["Lazer"] != str(cinema.id)
    assert uuid.UUID(settings.provider_category_map["Lazer"])


@pytest.mark.asyncio
async def test_organize_refusal_fails_run_with_friendly_message(
    session, test_workspace, monkeypatch
):
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize",
        trigger="manual", status="running", stats={}, detail={},
    )
    session.add(run)
    await session.commit()
    client = _fake_client(beta=lambda **_: _message({}, stop_reason="refusal"))
    monkeypatch.setattr(organizer, "resolve_client", AsyncMock(return_value=client))
    await organizer.organize(session, test_workspace.id, run)
    await session.commit()
    run = await _fresh(session, CategorizationRun, run.id)
    assert run.status == "failed"
    assert run.error == "refusal"
    assert run.detail["message"]
    assert run.cost_usd > 0  # a refused call is still paid for


@pytest.mark.asyncio
async def test_organize_capped_by_monthly_cap(session, test_workspace, monkeypatch):
    settings = await get_settings(session, test_workspace.id)
    settings.monthly_cap_usd = Decimal("0.05")
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize",
        trigger="manual", status="running", stats={}, detail={},
    )
    session.add(run)
    await session.commit()
    client = _fake_client(beta=lambda **_: pytest.fail("must not call over the cap"))
    monkeypatch.setattr(organizer, "resolve_client", AsyncMock(return_value=client))
    await organizer.organize(session, test_workspace.id, run)
    await session.commit()
    run = await _fresh(session, CategorizationRun, run.id)
    assert run.status == "capped"
    assert run.finished_at is not None
    client.beta.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_organize_without_connection_fails(session, test_workspace):
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize",
        trigger="manual", status="running", stats={}, detail={},
    )
    session.add(run)
    await session.commit()
    await organizer.organize(session, test_workspace.id, run)
    await session.commit()
    run = await _fresh(session, CategorizationRun, run.id)
    assert (run.status, run.error) == ("failed", "no_ai_connection")


# --- applying -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_reuses_old_category_id_keeping_budgets_and_rules(
    session, test_user, test_workspace, test_account
):
    outros = _group(session, test_user, test_workspace, "Outros")
    lazer = _cat(session, test_user, test_workspace, "Lazer", outros)
    _cat(session, test_user, test_workspace, "Diversos", outros)
    _cat(session, test_user, test_workspace, "Presentes", outros)
    await session.flush()
    budget = Budget(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        category_id=lazer.id, amount=Decimal("300"), month=date(2026, 10, 1),
    )
    rule = Rule(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="r",
        conditions=[], actions=[{"op": "set_category", "value": str(lazer.id)}],
    )
    recurring = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        category_id=lazer.id, description="Netflix", amount=Decimal("40"), currency="BRL",
        type="debit", frequency="monthly", start_date=date(2026, 1, 1),
        next_occurrence=date(2026, 11, 1),
    )
    session.add_all([budget, rule, recurring])
    run = await _organize_run(
        session, test_workspace,
        [_op("o1", "split_category", target="c1", name="Lazer",
             children=["Assinaturas", "Ingressos", "Cinema"])],
        {"c1": lazer.id},
    )

    result = await apply_op(session, test_workspace.id, run.id, "o1", test_user.id)
    await session.commit()

    lazer = await _fresh(session, Category, lazer.id)
    assert lazer.name == "Assinaturas"
    group = await _fresh(session, CategoryGroup, lazer.group_id)
    assert group.name == "Lazer" and group.id != outros.id
    children = (
        await session.execute(select(Category).where(Category.group_id == group.id))
    ).scalars().all()
    assert sorted(c.name for c in children) == ["Assinaturas", "Cinema", "Ingressos"]
    assert (await _fresh(session, Budget, budget.id)).category_id == lazer.id
    assert (await _fresh(session, Rule, rule.id)).actions[0]["value"] == str(lazer.id)
    assert (await _fresh(session, RecurringTransaction, recurring.id)).category_id == lazer.id

    redistribute = await _fresh(session, CategorizationRun, result["redistribute_run_id"])
    assert (redistribute.kind, redistribute.status) == ("redistribute", "queued")
    assert redistribute.detail["source_category_id"] == str(lazer.id)
    assert set(redistribute.detail["allowed_category_ids"]) == {str(c.id) for c in children}
    run = await _fresh(session, CategorizationRun, run.id)
    assert run.detail["ops"][0]["status"] == "applied"
    assert result["op"]["status"] == "applied"
    # Applying again is refused.
    with pytest.raises(organizer.OpConflict):
        await apply_op(session, test_workspace.id, run.id, "o1", test_user.id)


@pytest.mark.asyncio
async def test_split_redistribution_can_move_user_transactions_with_events(
    session, test_user, test_workspace, test_account, monkeypatch
):
    from app.agents.models.connection import LlmConnection
    from app.agents.services.crypto import encrypt

    session.add(LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="a", kind="anthropic",
        api_key_encrypted=encrypt("sk-ant-test"), is_default=True, extra={},
    ))
    lazer = _group(session, test_user, test_workspace, "Lazer")
    assinaturas = _cat(session, test_user, test_workspace, "Assinaturas", lazer)
    ingressos = _cat(session, test_user, test_workspace, "Ingressos", lazer)
    cinema = _cat(session, test_user, test_workspace, "Cinema", lazer)
    mercado = _cat(session, test_user, test_workspace, "Mercado")
    by_hand = _tx(session, test_user, test_account, "INGRESSO.COM", category=assinaturas, source="user")
    by_ai = _tx(session, test_user, test_account, "CINEMARK", category=assinaturas, source="ai")
    stays = _tx(session, test_user, test_account, "NETFLIX.COM", category=assinaturas, source="user")
    outside = _tx(session, test_user, test_account, "CARREFOUR", category=mercado, source="user")
    session.add(MerchantMemory(
        id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="INGRESSO.COM",
        counts={str(assinaturas.id): 3},
    ))
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="redistribute",
        trigger="manual", status="running", stats={},
        detail={
            "source_category_id": str(assinaturas.id),
            "allowed_category_ids": [str(c.id) for c in (assinaturas, ingressos, cinema)],
        },
    )
    session.add(run)
    await session.commit()

    sent = {}

    def create(**params):
        context = params["system"][1]["text"]
        shorts = {label.split(" › ")[-1]: short for short, label in _SHORT_RE.findall(context)}
        sent["shorts"] = shorts
        items = json.loads(params["messages"][0]["content"])
        sent["items"] = items
        wanted = {"INGRESSO": "Ingressos", "CINEMARK": "Cinema", "NETFLIX": "Assinaturas"}
        out = []
        for item in items:
            name = next(v for k, v in wanted.items() if k in item["s"][0])
            out.append({"k": item["k"], "n": None, "r": "ok", "c": shorts[name],
                        "alt": None, "p": 97, "new": None})
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({"items": out}))],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=10,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )

    client = _fake_client(create=create)
    monkeypatch.setattr(organizer, "resolve_client", AsyncMock(return_value=client))
    await organizer.redistribute(session, test_workspace.id, run)
    await session.commit()

    # Only the split's children were offered.
    assert set(sent["shorts"]) == {"Assinaturas", "Ingressos", "Cinema"}
    assert len(sent["items"]) == 3
    by_hand = await _fresh(session, Transaction, by_hand.id)
    by_ai = await _fresh(session, Transaction, by_ai.id)
    stays = await _fresh(session, Transaction, stays.id)
    outside = await _fresh(session, Transaction, outside.id)
    assert (by_hand.category_id, by_hand.category_source) == (ingressos.id, "ai")
    assert (by_ai.category_id, by_ai.category_source) == (cinema.id, "ai")
    assert (stays.category_id, stays.category_source) == (assinaturas.id, "user")
    assert (outside.category_id, outside.category_source) == (mercado.id, "user")
    event = (
        await session.execute(
            select(CategorizationEvent).where(CategorizationEvent.transaction_id == by_hand.id)
        )
    ).scalar_one()
    assert event.run_id == run.id
    assert (event.previous_source, event.previous_category_id) == ("user", assinaturas.id)
    run = await _fresh(session, CategorizationRun, run.id)
    assert run.status == "done"
    # The memory follows the rows the person agreed to move.
    memory = (
        await session.execute(
            select(MerchantMemory).where(MerchantMemory.merchant_key == "INGRESSO.COM")
        )
    ).scalar_one()
    await session.refresh(memory)
    assert memory.counts == {str(ingressos.id): 3}


@pytest.mark.asyncio
async def test_merge_moves_transactions_with_events(
    session, test_user, test_workspace, test_account
):
    group = _group(session, test_user, test_workspace, "Lazer")
    shows = _cat(session, test_user, test_workspace, "Shows", group)
    eventos = _cat(session, test_user, test_workspace, "Eventos", group)
    _cat(session, test_user, test_workspace, "Cinema", group)
    tx = _tx(session, test_user, test_account, "TICKETMASTER", category=eventos, source="user")
    session.add(MerchantMemory(
        id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="SPOTIFY",
        counts={}, ai_category_id=shows.id, ai_confidence=95,
    ))
    run = await _organize_run(
        session, test_workspace, [_op("o1", "merge", target="c2", into="c1")],
        {"c1": shows.id, "c2": eventos.id},
    )
    await apply_op(session, test_workspace.id, run.id, "o1", test_user.id)
    await session.commit()

    tx = await _fresh(session, Transaction, tx.id)
    assert (tx.category_id, tx.category_source) == (shows.id, "user")
    assert await _fresh(session, Category, eventos.id) is None
    event = (
        await session.execute(
            select(CategorizationEvent).where(CategorizationEvent.transaction_id == tx.id)
        )
    ).scalar_one()
    assert event.new_category_id == shows.id
    memory = (await session.execute(select(MerchantMemory))).scalar_one()
    await session.refresh(memory)
    assert memory.ai_category_id is None  # the tree changed: cached decisions go


@pytest.mark.asyncio
async def test_create_category_moves_its_stores(session, test_user, test_workspace, test_account):
    group = _group(session, test_user, test_workspace, "Lazer")
    lazer = _cat(session, test_user, test_workspace, "Lazer", group)
    rows = [
        _tx(session, test_user, test_account, "NETFLIX.COM", category=lazer, source="user")
        for _ in range(3)
    ]
    run = await _organize_run(
        session, test_workspace,
        [_op("o1", "create_category", group="g1", name="Streaming", children=["i1"])],
        {"g1": group.id, "c1": lazer.id},
        stores={"i1": {"key": "NETFLIX.COM", "tx": [str(r.id) for r in rows]}},
    )
    result = await apply_op(session, test_workspace.id, run.id, "o1", test_user.id)
    await session.commit()
    streaming = await _fresh(session, Category, uuid.UUID(result["op"]["result"]["category_id"]))
    assert (streaming.name, streaming.group_id) == ("Streaming", group.id)
    for row in rows:
        row = await _fresh(session, Transaction, row.id)
        assert (row.category_id, row.category_source) == (streaming.id, "ai")
    events = (await session.execute(select(CategorizationEvent))).scalars().all()
    assert {e.run_id for e in events} == {run.id}


@pytest.mark.asyncio
async def test_reject_and_stale_ops(session, test_user, test_workspace):
    group = _group(session, test_user, test_workspace, "Lazer")
    lazer = _cat(session, test_user, test_workspace, "Lazer", group)
    gone = uuid.uuid4()
    run = await _organize_run(
        session, test_workspace,
        [_op("o1", "rename", target="c1", name="Diversão"),
         _op("o2", "rename", target="c2", name="Nada")],
        {"c1": lazer.id, "c2": gone},
    )
    op = await organizer.reject_op(session, test_workspace.id, run.id, "o1")
    await session.commit()
    assert op["status"] == "rejected"
    assert (await _fresh(session, Category, lazer.id)).name == "Lazer"
    with pytest.raises(organizer.OpConflict):
        await apply_op(session, test_workspace.id, run.id, "o1", test_user.id)
    with pytest.raises(organizer.OpConflict):
        await apply_op(session, test_workspace.id, run.id, "o2", test_user.id)
    with pytest.raises(organizer.NotFound):
        await apply_op(session, test_workspace.id, run.id, "o9", test_user.id)


# --- Pluggy map ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pluggy_match_survives_category_rename(session, test_user, test_workspace):
    group = _group(session, test_user, test_workspace, "Lazer")
    lazer = _cat(session, test_user, test_workspace, "Lazer", group)
    run = await _organize_run(
        session, test_workspace, [_op("o1", "rename", target="c1", name="Entretenimento")],
        {"c1": lazer.id},
    )
    await apply_op(session, test_workspace.id, run.id, "o1", test_user.id)
    await session.commit()
    assert (await _fresh(session, Category, lazer.id)).name == "Entretenimento"
    assert await _match_pluggy_category(session, test_workspace.id, "Entertainment") == lazer.id
    assert await _match_pluggy_category(session, test_workspace.id, "Leisure") == lazer.id


@pytest.mark.asyncio
async def test_pluggy_map_ignores_foreign_or_hidden_ids(session, test_user, test_workspace):
    lazer = _cat(session, test_user, test_workspace, "Lazer")
    hidden = _cat(session, test_user, test_workspace, "Velho", is_hidden=True, is_system=True)
    settings = await get_settings(session, test_workspace.id)
    settings.provider_category_map = {"Lazer": str(hidden.id), "Saúde": str(uuid.uuid4())}
    await session.commit()
    # A hidden or unknown id falls back to the name.
    assert await _match_pluggy_category(session, test_workspace.id, "Leisure") == lazer.id
    assert await _match_pluggy_category(session, test_workspace.id, "Health") is None


# --- cross-workspace group ----------------------------------------------------------


@pytest.mark.asyncio
async def test_group_id_from_other_workspace_is_rejected(
    client, auth_headers, session, test_user, test_workspace
):
    other = Workspace(
        id=uuid.uuid4(), name="Outro", kind="personal", created_by_user_id=test_user.id,
        default_currency="BRL", locale="pt-BR",
    )
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    foreign = CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other.id, name="Deles",
        icon="folder", color="#000000",
    )
    mine = _group(session, test_user, test_workspace, "Meu")
    cat = _cat(session, test_user, test_workspace, "Lazer")
    session.add(foreign)
    await session.commit()
    foreign_id, mine_id, cat_id = foreign.id, mine.id, cat.id
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    with pytest.raises(category_service.CategoryGroupNotFoundError):
        await category_service.create_category(
            session, test_workspace.id, test_user.id,
            CategoryCreate(name="X", group_id=foreign_id),
        )
    with pytest.raises(category_service.CategoryGroupNotFoundError):
        await category_service.update_category(
            session, cat_id, test_workspace.id, CategoryUpdate(group_id=foreign_id)
        )
    await session.rollback()

    resp = await client.post("/api/categories", headers=headers,
                             json={"name": "X", "group_id": str(foreign_id)})
    assert resp.status_code == 422, resp.text
    resp = await client.patch(f"/api/categories/{cat_id}", headers=headers,
                              json={"group_id": str(foreign_id)})
    assert resp.status_code == 422, resp.text
    assert (await _fresh(session, Category, cat_id)).group_id is None
    resp = await client.patch(f"/api/categories/{cat_id}", headers=headers,
                              json={"group_id": str(mine_id)})
    assert resp.status_code == 200, resp.text


# --- index restriction --------------------------------------------------------------


def test_build_category_index_restricted_to_allowed_ids():
    group = CategoryGroup(id=uuid.uuid4(), name="Lazer", position=0, is_hidden=False)

    def cat(name):
        return Category(id=uuid.uuid4(), name=name, group_id=group.id,
                        is_hidden=False, treat_as_transfer=False)

    a, b, c = cat("A"), cat("B"), cat("C")
    index = build_category_index([a, b, c], [group], allowed_category_ids={a.id, c.id})
    assert [cid for _, cid, _ in index.entries] == [a.id, c.id]
    assert len(build_category_index([a, b, c], [group]).entries) == 3


# --- API ----------------------------------------------------------------------------


async def _member(session, client, workspace, role, email):
    import bcrypt

    from app.models.user import User

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
    return {"Authorization": f"Bearer {resp.json()['access_token']}", "X-Workspace-Id": str(workspace.id)}


@pytest.mark.asyncio
async def test_api_run_queues_and_returns_busy(
    client, auth_headers, session, test_workspace, organizer_enqueue
):
    resp = await client.post(f"{API}/organizer/run", headers=auth_headers)
    assert resp.status_code == 202, resp.text
    run_id = uuid.UUID(resp.json()["id"])
    run = await _fresh(session, CategorizationRun, run_id)
    assert (run.kind, run.status) == ("organize", "queued")
    organizer_enqueue.assert_called_once_with(test_workspace.id, run_id)

    again = await client.post(f"{API}/organizer/run", headers=auth_headers)
    assert again.status_code == 200
    assert again.json()["id"] == str(run_id)
    assert organizer_enqueue.call_count == 1


@pytest.mark.asyncio
async def test_api_run_503_when_enqueue_fails(client, auth_headers, session, organizer_enqueue):
    organizer_enqueue.return_value = False
    resp = await client.post(f"{API}/organizer/run", headers=auth_headers)
    assert resp.status_code == 503
    run = (await session.execute(select(CategorizationRun))).scalar_one()
    await session.refresh(run)
    assert (run.status, run.error) == ("failed", "enqueue_failed")


@pytest.mark.asyncio
async def test_api_get_apply_reject(
    client, auth_headers, session, test_user, test_workspace, organizer_enqueue
):
    assert (await client.get(f"{API}/organizer", headers=auth_headers)).json() == {
        "run": None, "ops": [], "message": None,
    }
    outros = _group(session, test_user, test_workspace, "Outros")
    lazer = _cat(session, test_user, test_workspace, "Lazer", outros)
    _cat(session, test_user, test_workspace, "Outros", outros)
    _cat(session, test_user, test_workspace, "Pets", outros)
    run = await _organize_run(
        session, test_workspace,
        [_op("o1", "rename", target="c1", name="Diversão"),
         _op("o2", "split_category", target="c1", name="Lazer", children=["Jogos", "Shows"])],
        {"c1": lazer.id},
    )
    body = (await client.get(f"{API}/organizer", headers=auth_headers)).json()
    assert body["run"]["id"] == str(run.id)
    assert [op["id"] for op in body["ops"]] == ["o1", "o2"]
    assert "ids" not in body and "stores" not in body

    resp = await client.post(f"{API}/organizer/ops/o1/reject", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    assert (await client.post(f"{API}/organizer/ops/o1/apply", headers=auth_headers)).status_code == 409
    assert (await client.post(f"{API}/organizer/ops/o9/apply", headers=auth_headers)).status_code == 404

    resp = await client.post(f"{API}/organizer/ops/o2/apply", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["op"]["status"] == "applied"
    assert payload["redistribute_run"]["kind"] == "redistribute"
    organizer_enqueue.assert_called_once_with(
        test_workspace.id, uuid.UUID(payload["redistribute_run"]["id"])
    )


@pytest.mark.asyncio
async def test_api_organizer_writes_need_manager(client, session, test_workspace, viewer_auth_headers):
    editor = await _member(session, client, test_workspace, "editor", "editor-org@example.com")
    assert (await client.get(f"{API}/organizer", headers=editor)).status_code == 200
    for path in ("/organizer/run", "/organizer/ops/o1/apply", "/organizer/ops/o1/reject"):
        assert (await client.post(f"{API}{path}", headers=editor)).status_code == 403
        assert (await client.post(f"{API}{path}", headers=viewer_auth_headers)).status_code == 403


# --- the Celery task ----------------------------------------------------------------


def test_organizer_task_is_registered_and_enqueue_never_raises():
    from kombu.exceptions import OperationalError

    from app.tasks import categorization_tasks
    from app.worker import celery_app

    assert categorization_tasks.ORGANIZE_TASK == "app.tasks.categorization_tasks.organize_workspace"
    assert categorization_tasks.organize_workspace.name == categorization_tasks.ORGANIZE_TASK
    assert "app.tasks.categorization_tasks" in celery_app.conf.include
    ws, run_id = uuid.uuid4(), uuid.uuid4()
    with pytest.MonkeyPatch.context() as mp:
        send = MagicMock()
        mp.setattr(celery_app, "send_task", send)
        assert real_enqueue_organizer(ws, run_id) is True
    send.assert_called_once_with(
        categorization_tasks.ORGANIZE_TASK, args=[str(ws), str(run_id)]
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(celery_app, "send_task", MagicMock(side_effect=OperationalError("down")))
        assert real_enqueue_organizer(ws, run_id) is False


@pytest.mark.asyncio
async def test_organize_task_claims_and_dispatches(session, test_workspace):
    from app.tasks.categorization_tasks import _organize

    def maker():
        return session

    queued = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize", trigger="manual",
        status="queued", stats={}, detail={},
    )
    empty_split = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="redistribute", trigger="manual",
        status="queued", stats={}, detail={"source_category_id": "x", "allowed_category_ids": []},
    )
    session.add_all([queued, empty_split])
    await session.commit()
    result = await _organize(maker, test_workspace.id, queued.id)
    assert result == {"status": "failed", "run_id": str(queued.id)}
    run = await _fresh(session, CategorizationRun, queued.id)
    assert (run.status, run.error) == ("failed", "no_ai_connection")
    assert await _organize(maker, test_workspace.id, queued.id) == {
        "status": "skipped", "reason": "run not queued",
    }
    result = await _organize(maker, test_workspace.id, empty_split.id)
    assert result["status"] == "failed"
    assert (await _fresh(session, CategorizationRun, empty_split.id)).error == "categories_gone"


@pytest.mark.asyncio
async def test_organize_task_records_unexpected_errors(session, test_workspace, monkeypatch):
    from app.tasks.categorization_tasks import _organize

    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize", trigger="manual",
        status="queued", stats={}, detail={},
    )
    session.add(run)
    await session.commit()
    monkeypatch.setattr(organizer, "organize", AsyncMock(side_effect=RuntimeError("boom")))
    result = await _organize(lambda: session, test_workspace.id, run.id)
    assert result["status"] == "failed"
    run = await _fresh(session, CategorizationRun, run.id)
    assert (run.status, run.error) == ("failed", "RuntimeError")


# --- Postgres: locks, savepoints, FKs, the batch path -------------------------------


def _batch_answer(request, category_name):
    context = request["params"]["system"][1]["text"]
    shorts = {label.split(" › ")[-1]: s for s, label in _SHORT_RE.findall(context)}
    items = json.loads(request["params"]["messages"][0]["content"])
    decided = [{"k": i["k"], "n": None, "r": "ok", "c": shorts[category_name], "alt": None,
                "p": 99, "new": None} for i in items]
    entry = SimpleNamespace(
        custom_id=request["custom_id"],
        result=SimpleNamespace(type="succeeded", message=SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({"items": decided}))],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=10,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )),
    )
    return set(shorts), entry


async def test_organizer_end_to_end_postgres(postgres_sessions, monkeypatch):
    """Advisory lock, FOR UPDATE, the merge's savepointed delete and a
    redistribution large enough for a Message Batch, on Postgres."""
    from app.agents.models.connection import LlmConnection
    from app.agents.services.crypto import encrypt
    from app.models.account import Account
    from app.tasks import categorization_tasks
    from tests.test_admin_service import _make_user
    from tests.test_categorization_classifier import _names

    async with postgres_sessions() as session:
        user = await _make_user(session, f"org_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.flush()
        ws_id = account.workspace_id
        ws = SimpleNamespace(id=ws_id)
        session.add(LlmConnection(
            id=uuid.uuid4(), user_id=user.id, name="a", kind="anthropic",
            api_key_encrypted=encrypt("sk-ant-test"), is_default=True, extra={},
        ))
        group = _group(session, user, ws, "Diversão")
        lazer = _cat(session, user, ws, "Lazer", group)
        shows = _cat(session, user, ws, "Shows", group)
        eventos = _cat(session, user, ws, "Eventos", group)
        _cat(session, user, ws, "Bares", group)
        await session.flush()
        names = list(_names(101))
        for name in names:
            _tx(session, user, account, name, category=lazer, source="user")
        _tx(session, user, account, "TICKETMASTER", category=eventos, source="ai")
        run = CategorizationRun(
            id=uuid.uuid4(), workspace_id=ws_id, kind="organize", trigger="manual",
            status="queued", stats={}, detail={},
        )
        session.add(run)
        await session.commit()
        user_id = user.id
        ids = {"lazer": lazer.id, "shows": shows.id, "eventos": eventos.id, "run": run.id}

    def propose(**params):
        data = json.loads(params["messages"][0]["content"])
        cats = {c["name"]: c["c"] for g in data["groups"] for c in g["cats"]}
        return _message({"ops": [
            _op("o1", "merge", target=cats["Eventos"], into=cats["Shows"]),
            _op("o2", "split_category", target=cats["Lazer"], name="Lazer",
                children=["Jogos", "Passeios"]),
        ]})

    monkeypatch.setattr(
        organizer, "resolve_client", AsyncMock(return_value=_fake_client(beta=propose))
    )
    result = await categorization_tasks._organize(postgres_sessions, ws_id, ids["run"])
    assert result["status"] == "done", result

    async with postgres_sessions() as session:
        run = await session.get(CategorizationRun, ids["run"])
        assert run is not None and [op["id"] for op in run.detail["ops"]] == ["o1", "o2"]
        await apply_op(session, ws_id, ids["run"], "o1", user_id)
        await session.commit()
        applied = await apply_op(session, ws_id, None, "o2", user_id)
        await session.commit()
        redistribute_id = applied["redistribute_run_id"]
        assert await session.get(Category, ids["eventos"]) is None
        moved = (
            await session.execute(
                select(Transaction).where(Transaction.description == "TICKETMASTER")
            )
        ).scalar_one()
        assert (moved.category_id, moved.category_source) == (ids["shows"], "ai")
        children = applied["op"]["result"]["category_ids"]
        assert children[0] == str(ids["lazer"])

    # 101 stores: the redistribution goes through a Message Batch.
    batch_client = MagicMock()
    batch_client.close = AsyncMock()
    batch_client.messages.batches.create = AsyncMock(return_value=SimpleNamespace(id="batch_1"))
    monkeypatch.setattr(organizer, "resolve_client", AsyncMock(return_value=batch_client))
    result = await categorization_tasks._organize(postgres_sessions, ws_id, redistribute_id)
    assert result["status"] == "processing_batch"

    sent = batch_client.messages.batches.create.await_args
    assert sent is not None
    entries = []
    for request in sent.kwargs["requests"]:
        offered, entry = _batch_answer(request, "Passeios")
        assert offered == {"Jogos", "Passeios"}  # only the split's children
        entries.append(entry)
    collector = MagicMock()
    collector.close = AsyncMock()
    collector.messages.batches.retrieve = AsyncMock(
        return_value=SimpleNamespace(processing_status="ended")
    )

    async def decoder():
        for entry in entries:
            yield entry

    collector.messages.batches.results = AsyncMock(side_effect=lambda _id: decoder())
    monkeypatch.setattr(classifier, "resolve_client", AsyncMock(return_value=collector))
    assert await categorization_tasks._poll(postgres_sessions) == {
        "collected": 1, "pending": 0, "failed": 0,
    }

    async with postgres_sessions() as session:
        run = await session.get(CategorizationRun, redistribute_id)
        assert run is not None and run.status == "done"
        assert run.detail["source_category_id"] == str(ids["lazer"])
        rows = (
            await session.execute(select(Transaction).where(Transaction.description.in_(names)))
        ).scalars().all()
        assert len(rows) == 101
        passeios = uuid.UUID(children[1])
        assert {(r.category_id, r.category_source) for r in rows} == {(passeios, "ai")}
        events = (
            await session.execute(
                select(CategorizationEvent).where(CategorizationEvent.run_id == redistribute_id)
            )
        ).scalars().all()
        assert len(events) == 101
        assert {e.previous_source for e in events} == {"user"}
