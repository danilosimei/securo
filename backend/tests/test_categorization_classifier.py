"""The Claude classifier: prompts out, decisions in, under the monthly cap.

The SDK client is a mock: `messages.create` answers from what the request
carries (item ids and the workspace's short category ids), so the tests
exercise the real prompt building and the real id mapping.
"""
import itertools
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select, update

from app.agents.models.connection import LlmConnection
from app.agents.services.crypto import encrypt
from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.models.category import Category
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services.categorization import classifier, pipeline
from app.services.categorization.classifier import (
    SUPERSEDED_REASON,
    UNDECIDED_REASON,
    apply_results,
)
from app.services.categorization.costs import cost_usd
from app.services.categorization.memory import learn_choice, set_note
from app.services.categorization.pipeline import (
    NO_AI_REASON,
    collect_candidates,
    group_by_key,
    run_categorization,
)
from app.services.categorization.prompts import CLASSIFIER_MODEL, build_category_index
from app.services.categorization.settings import get_settings
from app.tasks import categorization_tasks

_SHORT_RE = re.compile(r"^(c\d+) (.+)$", re.MULTILINE)
#: What the Message Batches API accepts as `custom_id`.
CUSTOM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


# --- fixtures and helpers ---------------------------------------------------------


def _tx(session, user, account, description, *, source="none", category_id=None, **extra):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal(extra.pop("amount", "10.00")),
        date=date(2026, 9, 10),
        effective_date=date(2026, 9, 10),
        type="debit",
        source="sync",
        status="posted",
        category_id=category_id,
        category_source=source,
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    return tx


@pytest.fixture
async def cats(session, test_user, test_workspace):
    made = {}
    for name in ("Alimentação", "Transporte", "Lazer"):
        cat = Category(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            name=name, icon="x", color="#000000",
        )
        session.add(cat)
        made[name] = cat.id
    await session.commit()
    return made


@pytest.fixture
async def ai_connection(session, test_user, test_workspace):
    connection = LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="a", kind="anthropic",
        api_key_encrypted=encrypt("sk-ant-test"), is_default=True, extra={},
    )
    session.add(connection)
    await session.commit()
    return connection


def usage(i=100, o=50, cw=0, cr=0):
    return SimpleNamespace(
        input_tokens=i, output_tokens=o,
        cache_creation_input_tokens=cw, cache_read_input_tokens=cr,
    )


def message(items, *, stop_reason="end_turn", use=None):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"items": items}))],
        stop_reason=stop_reason,
        usage=use or usage(),
    )


def shorts(params) -> dict[str, str]:
    """Category name -> short id, read from the cached context block."""
    context = params["system"][1]["text"]
    return {label.split(": ")[0].split(" › ")[-1]: short for short, label in _SHORT_RE.findall(context)}


def sent_items(params) -> list[dict]:
    return json.loads(params["messages"][0]["content"])


def answer(decide, *, stop_reason="end_turn", use=None):
    """`messages.create` side effect: `decide(item, shorts)` -> output item or None."""

    def create(**params):
        ids = shorts(params)
        out = []
        for item in sent_items(params):
            decided = decide(item, ids)
            if decided is not None:
                out.append({"k": item["k"], **decided})
        return message(out, stop_reason=stop_reason, use=use)

    return create


def out(c=None, *, p=95, r="motivo", n=None, alt=None, new=None):
    return {"n": n, "r": r, "c": c, "alt": alt, "p": p, "new": new}


def fake_client(create=None, *, results=(), status="ended"):
    client = MagicMock()
    client.close = AsyncMock()
    client.messages.create = AsyncMock(side_effect=create)
    client.messages.batches.create = AsyncMock(return_value=SimpleNamespace(id="batch_1"))
    client.messages.batches.retrieve = AsyncMock(
        return_value=SimpleNamespace(processing_status=status)
    )

    async def decoder():
        for entry in results:
            yield entry

    client.messages.batches.results = AsyncMock(side_effect=lambda _id: decoder())
    return client


@pytest.fixture
def wire(monkeypatch):
    """Route the pipeline's classifier seam to a fake client."""

    def install(client):
        monkeypatch.setattr(pipeline, "ai_classifier", classifier.classify_with_claude)
        monkeypatch.setattr(classifier, "resolve_client", AsyncMock(return_value=client))
        return client

    return install


async def _all(session, model):
    return list((await session.execute(select(model))).scalars().all())


async def _reload(session, obj):
    return await session.get(type(obj), obj.id, populate_existing=True)


def _session_maker(session):
    def make():
        return session

    return make


# --- the brief's tests ------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_confidence_applies_as_ai_with_reason(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    client = wire(fake_client(answer(
        lambda item, ids: out(ids["Alimentação"], p=96, r="delivery de comida", n="iFood")
    )))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    client.messages.create.assert_awaited_once()
    tx = await _reload(session, tx)
    assert tx.category_id == cats["Alimentação"]
    assert tx.category_source == "ai"
    [event] = await _all(session, CategorizationEvent)
    assert (event.source, event.confidence, event.reason, event.model, event.run_id) == (
        "ai", 96, "delivery de comida", CLASSIFIER_MODEL, run.id,
    )
    [payee] = await _all(session, Payee)
    assert (payee.name, payee.source) == ("iFood", "ai")
    assert tx.payee_id == payee.id
    assert (event.previous_payee_id, event.new_payee_id) == (None, payee.id)
    [memory] = await _all(session, MerchantMemory)
    assert (memory.merchant_key, memory.ai_category_id, memory.ai_confidence) == (
        "IFOOD", cats["Alimentação"], 96,
    )
    assert memory.ai_clean_name == "iFood"
    assert run.status == "done" and run.finished_at is not None
    assert run.model == CLASSIFIER_MODEL
    assert run.stats["applied"] == 1 and run.stats["suggested"] == 0
    assert await _all(session, CategorizationSuggestion) == []


@pytest.mark.asyncio
async def test_low_confidence_creates_suggestion_with_alt(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    tx = _tx(session, test_user, test_account, "NETFLIX.COM")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(
        ids["Lazer"], alt=ids["Alimentação"], p=70, r="assinatura de streaming",
        n="Netflix", new={"g": "Assinaturas", "name": "Streaming"},
    ))))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    tx = await _reload(session, tx)
    assert (tx.category_id, tx.category_source, tx.payee_id) == (None, "none", None)
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.status == "pending" and sug.transaction_id == tx.id
    assert (sug.category_id, sug.alt_category_id) == (cats["Lazer"], cats["Alimentação"])
    assert (sug.confidence, sug.reason, sug.clean_name) == (70, "assinatura de streaming", "Netflix")
    assert sug.new_category == {"group": "Assinaturas", "name": "Streaming"}
    assert sug.run_id == run.id and sug.merchant_key == "NETFLIX.COM"
    assert run.stats["suggested"] == 1 and run.stats["applied"] == 0
    assert await _all(session, CategorizationEvent) == []
    assert await _all(session, Payee) == []


@pytest.mark.asyncio
async def test_apply_results_drops_unknown_ids_and_handles_refusal(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    a = _tx(session, test_user, test_account, "LOJA ALFA")
    b = _tx(session, test_user, test_account, "LOJA BETA")
    c = _tx(session, test_user, test_account, "LOJA GAMA")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))
    by_item = {"i1": groups["LOJA ALFA"], "i2": groups["LOJA BETA"], "i3": groups["LOJA GAMA"]}
    index = build_category_index(await _all(session, Category), [])
    alim = index.to_short(cats["Alimentação"])
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="manual",
        status="running", stats={}, detail={},
    )
    session.add(run)
    await session.flush()

    stats = await apply_results(
        session, test_workspace.id, run, by_item,
        [
            {"k": "i99", **out(alim)},  # unknown item: ignored
            {"k": "i1", **out("c999", p=99)},  # invalid short id: no category
            {"k": "i2", **out(alim, p=97)},
            "garbage",
        ],
        index=index, threshold=90, model=CLASSIFIER_MODEL,
    )

    assert (stats.applied, stats.suggested, stats.skipped) == (1, 1, 1)
    assert (await _reload(session, b)).category_id == cats["Alimentação"]
    sugs = {s.transaction_id: s for s in await _all(session, CategorizationSuggestion)}
    assert set(sugs) == {a.id, c.id}
    assert sugs[a.id].category_id is None and sugs[a.id].confidence == 99
    assert sugs[c.id].category_id is None and sugs[c.id].reason == UNDECIDED_REASON

    # A refused call: every store of the batch goes to review, undecided.
    d = _tx(session, test_user, test_account, "LOJA DELTA")
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: out(ids["Lazer"]), stop_reason="refusal")))
    refused = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    client.messages.create.assert_awaited_once()
    [sug] = [s for s in await _all(session, CategorizationSuggestion) if s.transaction_id == d.id]
    assert (sug.category_id, sug.reason, sug.status) == (None, UNDECIDED_REASON, "pending")
    assert (await _reload(session, d)).category_source == "none"
    assert refused.stats["skipped"] >= 1 and refused.stats["applied"] == 0

    # max_tokens is just as undecided.
    e = _tx(session, test_user, test_account, "LOJA EPSILON")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Lazer"]), stop_reason="max_tokens")))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    [sug] = [s for s in await _all(session, CategorizationSuggestion) if s.transaction_id == e.id]
    assert (sug.category_id, sug.reason) == (None, UNDECIDED_REASON)


@pytest.mark.asyncio
async def test_clean_name_reuses_payee_and_never_renames(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    existing = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="ifood", source="ai",
    )
    session.add(existing)
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], n="  iFood "))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert (await _reload(session, tx)).payee_id == existing.id
    [payee] = await _all(session, Payee)
    assert payee.name == "ifood"  # never renamed


@pytest.mark.asyncio
async def test_payee_that_would_change_the_store_key_is_not_set(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    # A same-name payee typed by hand would regroup the row under "P:UBER",
    # splitting it from the store its memory lives under: left alone.
    manual = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Uber", source="manual",
    )
    session.add(manual)
    tx = _tx(session, test_user, test_account, "UBER *TRIP")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Transporte"], n="Uber"))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    tx = await _reload(session, tx)
    assert tx.category_id == cats["Transporte"] and tx.payee_id is None
    assert [p.id for p in await _all(session, Payee)] == [manual.id]


@pytest.mark.asyncio
async def test_manual_payee_is_kept(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    manual = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Padaria do Zé", source="manual",
    )
    session.add(manual)
    await session.flush()
    tx = _tx(session, test_user, test_account, "PADARIA ZE 123", payee_id=manual.id)
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], n="Padaria Zé"))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    # A payee names a `P:` store, which only ever gets a suggestion: the
    # manual payee is neither replaced nor duplicated.
    tx = await _reload(session, tx)
    assert tx.category_id is None and tx.payee_id == manual.id
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.category_id == cats["Alimentação"]
    assert [p.name for p in await _all(session, Payee)] == ["Padaria do Zé"]


@pytest.mark.asyncio
async def test_classifier_stops_when_cap_reached(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    session.add(CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="ingest",
        status="done", cost_usd=Decimal("0.999999"), stats={}, detail={},
    ))
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"]))))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    client.messages.create.assert_not_awaited()
    client.messages.batches.create.assert_not_awaited()
    assert run.status == "capped" and run.finished_at is not None
    [sug] = await _all(session, CategorizationSuggestion)
    assert (sug.transaction_id, sug.reason, sug.category_id) == (tx.id, NO_AI_REASON, None)
    assert run.stats["suggested"] == 1
    assert run.cost_usd == 0


def _names(count):
    letters = "KQWXZ"
    for combo in itertools.islice(itertools.product(letters, repeat=3), count):
        yield "LOJA " + "".join(combo)


@pytest.mark.asyncio
async def test_large_backlog_uses_batch_and_poll_applies(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire,
    categorization_enqueue,
):
    txs = [_tx(session, test_user, test_account, name) for name in _names(120)]
    await session.commit()
    client = wire(fake_client())

    prepared = await pipeline.prepare_run(session, test_workspace.id, trigger="backlog", use_ai=True)
    await session.commit()
    # Another worker marks the run while the batch is being submitted.
    await session.execute(
        update(CategorizationRun)
        .where(CategorizationRun.id == prepared.run.id)
        .values(detail={"rerun": True})
    )
    await session.commit()
    run = await pipeline.complete_run(session, test_workspace.id, prepared, use_ai=True)
    await session.commit()

    client.messages.create.assert_not_awaited()
    [call] = client.messages.batches.create.await_args_list
    requests = call.kwargs["requests"]
    assert [r["custom_id"] for r in requests] == [f"{run.id.hex}-0", f"{run.id.hex}-1"]
    assert all(CUSTOM_ID_RE.fullmatch(r["custom_id"]) for r in requests)
    assert [len(sent_items(r["params"])) for r in requests] == [100, 20]
    run = await _reload(session, run)
    assert (run.status, run.batch_id, run.is_batch) == ("processing_batch", "batch_1", True)
    assert run.finished_at is None
    assert run.detail["rerun"] is True and "batch" in run.detail
    assert await _all(session, CategorizationSuggestion) == []
    # Nothing goes out to a second worker while the batch is pending.
    assert (await categorization_tasks._categorize(
        _session_maker(session), test_workspace.id, "ingest", None
    )) == {"status": "skipped", "reason": "busy"}

    # The batch ends: chunk 0 answered, chunk 1 expired.
    other_run = uuid.uuid4().hex
    params0 = requests[0]["params"]
    ids = shorts(params0)
    decided = [{"k": item["k"], **out(ids["Lazer"], p=95)} for item in sent_items(params0)]
    ok = SimpleNamespace(type="succeeded", message=message(decided, use=usage(1000, 400)))
    results = [
        SimpleNamespace(custom_id=f"{run.id.hex}-1", result=SimpleNamespace(type="expired")),
        SimpleNamespace(custom_id=f"{other_run}-0", result=ok),  # not this run's: ignored
        SimpleNamespace(custom_id=f"{run.id.hex}-0", result=ok),
    ]
    client = wire(fake_client(results=results))
    summary = await categorization_tasks._poll(_session_maker(session))

    assert summary == {"collected": 1, "pending": 0, "failed": 0}
    run = await _reload(session, run)
    assert run.status == "done" and run.finished_at is not None
    assert "batch" not in run.detail and "rerun" not in run.detail
    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")
    assert run.cost_usd == cost_usd(CLASSIFIER_MODEL, usage(1000, 400), batch=True)
    assert (run.input_tokens, run.output_tokens) == (1000, 400)
    applied = [t for t in txs if (await _reload(session, t)).category_source == "ai"]
    assert len(applied) == 100
    undecided = await _all(session, CategorizationSuggestion)
    assert len(undecided) == 20
    assert {s.reason for s in undecided} == {UNDECIDED_REASON}
    assert run.stats["applied"] == 100
    assert run.stats["skipped"] == 20

    # Polling again finds nothing to do.
    assert await categorization_tasks._poll(_session_maker(session)) == {
        "collected": 0, "pending": 0, "failed": 0,
    }


@pytest.mark.asyncio
async def test_poll_leaves_unfinished_batches(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    for name in _names(101):
        _tx(session, test_user, test_account, name)
    await session.commit()
    wire(fake_client())
    run = await run_categorization(session, test_workspace.id, trigger="backlog", use_ai=True)
    await session.commit()

    wire(fake_client(status="in_progress"))
    assert await categorization_tasks._poll(_session_maker(session)) == {
        "collected": 0, "pending": 1, "failed": 0,
    }
    assert (await _reload(session, run)).status == "processing_batch"


@pytest.mark.asyncio
async def test_batch_beyond_the_cap_sends_only_what_fits(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, monkeypatch
):
    for name in _names(120):
        _tx(session, test_user, test_account, name)
    await session.commit()
    monkeypatch.setattr(classifier, "estimate_cost", lambda params, *, batch: Decimal("0.60"))
    client = wire(fake_client())

    run = await run_categorization(session, test_workspace.id, trigger="backlog", use_ai=True)

    [call] = client.messages.batches.create.await_args_list
    assert len(call.kwargs["requests"]) == 1
    assert run.status == "processing_batch" and run.detail["batch"]["capped"] is True
    # The stores left out wait for the person, revisitable next month.
    sugs = await _all(session, CategorizationSuggestion)
    assert len(sugs) == 20 and {s.reason for s in sugs} == {NO_AI_REASON}
    await session.commit()

    client = wire(fake_client(results=[]))
    await categorization_tasks._poll(_session_maker(session))
    assert (await _reload(session, run)).status == "capped"


@pytest.mark.asyncio
async def test_run_cost_accumulates(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    spent = usage(1200, 300, cw=4000, cr=0)
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"]), use=spent)))
    prepared = await pipeline.prepare_run(session, test_workspace.id, trigger="manual", use_ai=True)
    prepared.run.cost_usd = Decimal("0.010000")
    prepared.run.input_tokens = 5

    run = await pipeline.complete_run(session, test_workspace.id, prepared, use_ai=True)

    assert run.cost_usd == Decimal("0.010000") + cost_usd(CLASSIFIER_MODEL, spent, batch=False)
    assert (run.input_tokens, run.output_tokens, run.cache_write_tokens, run.cache_read_tokens) == (
        1205, 300, 4000, 0,
    )


# --- carry-overs ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_ai_suggestion_is_superseded_once_ai_exists_but_refusals_block(
    session, test_user, test_workspace, test_account, cats, wire
):
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    other = _tx(session, test_user, test_account, "LOJA RECUSADA")
    await session.commit()
    # No connection yet: suggestions without a model.
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    old = {s.transaction_id: s for s in await _all(session, CategorizationSuggestion)}
    assert {s.reason for s in old.values()} == {NO_AI_REASON}

    session.add(LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="a", kind="anthropic",
        api_key_encrypted=encrypt("sk-ant-test"), is_default=True, extra={},
    ))
    await session.commit()

    def decide(item, ids):
        if "IFOOD" in item["s"][0]:
            return out(ids["Alimentação"], p=95)
        return None  # the model leaves the other store out

    client = wire(fake_client(answer(decide)))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert len(sent_items(client.messages.create.await_args.kwargs)) == 2
    assert (await _reload(session, tx)).category_source == "ai"
    for sug in old.values():
        sug = await _reload(session, sug)
        assert (sug.status, sug.reason) == ("dismissed", SUPERSEDED_REASON)
        assert sug.resolved_at is not None
    pending = [s for s in await _all(session, CategorizationSuggestion) if s.status == "pending"]
    assert [(s.transaction_id, s.reason) for s in pending] == [(other.id, UNDECIDED_REASON)]

    # The undecided store is not paid for again.
    client = wire(fake_client(answer(decide)))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    client.messages.create.assert_not_awaited()

    # A superseded suggestion is not the person dismissing the store: once
    # the AI's decision is undone, the row is a candidate again.
    from app.services.categorization.events import undo

    [event] = await _all(session, CategorizationEvent)
    await undo(session, test_workspace.id, event_ids=[event.id], user_id=test_user.id)
    await session.commit()
    assert tx.id in {c.tx_id for c in await collect_candidates(session, test_workspace.id)}


@pytest.mark.asyncio
async def test_memory_supersedes_no_ai_suggestion(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    sug = CategorizationSuggestion(
        id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=tx.id,
        merchant_key="IFOOD", status="pending", reason=NO_AI_REASON,
    )
    session.add(sug)
    for _ in range(2):
        await learn_choice(session, test_workspace.id, "IFOOD", cats["Alimentação"])
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: out(ids["Lazer"]))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    client.messages.create.assert_not_awaited()
    assert (await _reload(session, tx)).category_source == "memory"
    assert (await _reload(session, sug)).status == "dismissed"


@pytest.mark.asyncio
async def test_rows_changed_while_claude_answered_are_rechecked(
    session, test_user, test_workspace, test_account, cats, ai_connection
):
    a1 = _tx(session, test_user, test_account, "LOJA ALFA", amount="1")
    a2 = _tx(session, test_user, test_account, "LOJA ALFA", amount="2")
    a3 = _tx(session, test_user, test_account, "LOJA ALFA", amount="3")
    a4 = _tx(session, test_user, test_account, "LOJA ALFA", amount="4")
    b1 = _tx(session, test_user, test_account, "LOJA BETA", amount="1")
    b2 = _tx(session, test_user, test_account, "LOJA BETA", amount="2")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))
    index = build_category_index(await _all(session, Category), [])
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="manual",
        status="running", stats={}, detail={},
    )
    session.add(run)
    # While the model answered: a rule took a2, a review question opened on
    # a3, a4 was ignored; on store BETA a person picked another category.
    a2.category_source, a2.category_id = "rule", cats["Transporte"]
    a4.is_ignored = True
    session.add(CategorizationSuggestion(
        id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=a3.id,
        merchant_key="LOJA ALFA", status="pending", reason="outra coisa",
        category_id=cats["Lazer"],
    ))
    b1.category_source, b1.category_id = "user", cats["Transporte"]
    await session.commit()
    lazer = index.to_short(cats["Lazer"])

    stats = await apply_results(
        session, test_workspace.id, run,
        {"i1": groups["LOJA ALFA"], "i2": groups["LOJA BETA"]},
        [{"k": "i1", **out(lazer, p=99)}, {"k": "i2", **out(lazer, p=99)}],
        index=index, threshold=90, model=CLASSIFIER_MODEL,
    )

    assert (await _reload(session, a1)).category_source == "ai"
    assert (await _reload(session, a2)).category_source == "rule"
    assert (await _reload(session, a3)).category_source == "none"
    assert (await _reload(session, a4)).category_source == "none"
    assert (await _reload(session, b1)).category_id == cats["Transporte"]
    # The person disagreed on that store: the rest becomes a question.
    b2 = await _reload(session, b2)
    assert b2.category_source == "none"
    [b2_sug] = [
        s for s in await _all(session, CategorizationSuggestion) if s.transaction_id == b2.id
    ]
    assert (b2_sug.category_id, b2_sug.confidence) == (cats["Lazer"], 99)
    assert (stats.applied, stats.suggested) == (1, 1)
    memory = {m.merchant_key: m for m in await _all(session, MerchantMemory)}
    assert memory["LOJA ALFA"].ai_category_id == cats["Lazer"]
    assert "LOJA BETA" not in memory or memory["LOJA BETA"].ai_category_id is None


@pytest.mark.asyncio
async def test_category_gone_before_apply_is_no_decision(
    session, test_user, test_workspace, test_account, cats, ai_connection
):
    tx = _tx(session, test_user, test_account, "LOJA ALFA")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))
    index = build_category_index(await _all(session, Category), [])
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="manual",
        status="running", stats={}, detail={},
    )
    session.add(run)
    lazer = await session.get(Category, cats["Lazer"])
    lazer.is_hidden = True
    await session.commit()

    await apply_results(
        session, test_workspace.id, run, {"i1": groups["LOJA ALFA"]},
        [{"k": "i1", **out(index.to_short(cats["Lazer"]), p=99, alt=index.to_short(cats["Lazer"]))}],
        index=index, threshold=90, model=CLASSIFIER_MODEL,
    )

    assert (await _reload(session, tx)).category_source == "none"
    [sug] = await _all(session, CategorizationSuggestion)
    assert (sug.category_id, sug.alt_category_id) == (None, None)


@pytest.mark.asyncio
async def test_model_output_is_sanitized(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    high = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    low = _tx(session, test_user, test_account, "LOJA BETA")
    await session.commit()

    def decide(item, ids):
        if "IFOOD" in item["s"][0]:
            return out(ids["Alimentação"], p=150, r="x" * 300, alt=ids["Alimentação"])
        return out(ids["Lazer"], p=-5, alt=ids["Lazer"], new={"g": " ", "name": ""})

    wire(fake_client(answer(decide)))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert (await _reload(session, high)).category_source == "ai"
    [event] = await _all(session, CategorizationEvent)
    assert event.confidence == 100 and event.reason == "x" * 120
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.transaction_id == low.id
    assert (sug.category_id, sug.alt_category_id, sug.confidence) == (cats["Lazer"], None, 0)
    assert sug.new_category is None


@pytest.mark.asyncio
async def test_history_is_sent_for_inconsistent_stores(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    _tx(session, test_user, test_account, "UBER *TRIP")
    await learn_choice(session, test_workspace.id, "IFOOD", cats["Alimentação"])
    await learn_choice(session, test_workspace.id, "IFOOD", cats["Lazer"])
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: None)))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    params = client.messages.create.await_args.kwargs
    ids = shorts(params)
    by_sample = {item["s"][0]: item for item in sent_items(params)}
    ifood = next(item for sample, item in by_sample.items() if "IFOOD" in sample)
    uber = next(item for sample, item in by_sample.items() if "UBER" in sample)
    assert sorted(ifood["h"]) == sorted([[ids["Alimentação"], 1], [ids["Lazer"], 1]])
    assert "h" not in uber


@pytest.mark.asyncio
async def test_classify_sends_memory_notes(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    _tx(session, test_user, test_account, "UBER *TRIP")
    await set_note(session, test_workspace.id, "IFOOD", "Aurino: jardineiro cpf 123.456.789-09")
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: None)))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    params = client.messages.create.await_args.kwargs
    content = params["messages"][0]["content"]
    assert "Aurino: jardineiro cpf" in content and "123.456.789-09" not in content
    by_sample = {item["s"][0]: item for item in sent_items(params)}
    ifood = next(item for sample, item in by_sample.items() if "IFOOD" in sample)
    uber = next(item for sample, item in by_sample.items() if "UBER" in sample)
    assert ifood["o"] == "Aurino: jardineiro cpf"
    assert "o" not in uber


@pytest.mark.asyncio
async def test_prompt_carries_descriptions_and_corrections(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    cat = await session.get(Category, cats["Lazer"])
    cat.description = "Bares e passeios. Não inclui delivery."
    old = _tx(session, test_user, test_account, "CINEMARK 33", source="user", category_id=cats["Lazer"])
    await session.flush()
    session.add(CategorizationEvent(
        id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=old.id, source="user",
        previous_source="ai", previous_category_id=cats["Transporte"],
        new_category_id=cats["Lazer"],
    ))
    _tx(session, test_user, test_account, "NETFLIX.COM")
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: out(ids["Lazer"], p=95))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    context = client.messages.create.await_args.kwargs["system"][1]["text"]
    assert "Lazer: Bares e passeios. Não inclui delivery." in context
    assert "CINEMARK -> c2 (não c3)" in context


@pytest.mark.asyncio
async def test_without_a_client_the_seam_falls_back_to_suggestions(
    session, test_user, test_workspace, test_account, cats, monkeypatch
):
    monkeypatch.setattr(pipeline, "ai_classifier", classifier.classify_with_claude)
    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert run.status == "done" and run.stats["suggested"] == 1
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.reason == NO_AI_REASON


@pytest.mark.asyncio
async def test_nameless_store_is_held_for_review_without_calling_the_model(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    """`UNKNOWN` rows would reach the model with only amount/date/type: no
    call is paid for them. They wait as a blocking, model-less suggestion
    (not `NO_AI_REASON`), so no later run re-collects or re-sends them."""
    from app.services.categorization.merchant_key import UNKNOWN_KEY

    nameless = [_tx(session, test_user, test_account, "") for _ in range(2)]
    named = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"]))))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert client.messages.create.await_count == 1
    sent = [item for call in client.messages.create.await_args_list
            for item in sent_items(call.kwargs)]
    assert len(sent) == 1 and "IFOOD" in sent[0]["s"][0]
    assert (await _reload(session, named)).category_source == "ai"
    held = [s for s in await _all(session, CategorizationSuggestion)
            if s.transaction_id in {tx.id for tx in nameless}]
    assert {s.transaction_id for s in held} == {tx.id for tx in nameless}
    assert {s.merchant_key for s in held} == {UNKNOWN_KEY}
    assert {(s.status, s.category_id, s.reason) for s in held} == {
        ("pending", None, pipeline.UNNAMED_REASON)
    }
    assert pipeline.UNNAMED_REASON != NO_AI_REASON
    assert run.stats["suggested"] == 2

    client.messages.create.reset_mock()
    again = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert client.messages.create.await_count == 0
    assert again.stats["suggested"] == 0
    assert len(await _all(session, CategorizationSuggestion)) == len(held)


@pytest.mark.asyncio
async def test_nameless_store_without_a_model_is_also_blocking(
    session, test_user, test_workspace, test_account, cats, monkeypatch
):
    """Even with no model, the nameless store's reason is not the one a later
    run with a model revisits."""
    monkeypatch.setattr(pipeline, "ai_classifier", classifier.classify_with_claude)
    tx = _tx(session, test_user, test_account, "")
    await session.commit()

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    [sug] = await _all(session, CategorizationSuggestion)
    assert (sug.transaction_id, sug.reason) == (tx.id, pipeline.UNNAMED_REASON)


def test_classifier_is_wired_into_the_pipeline():
    assert pipeline.ai_classifier is classifier.classify_with_claude


@pytest.mark.asyncio
async def test_settings_threshold_decides_apply_or_suggest(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    settings = await get_settings(session, test_workspace.id)
    settings.auto_apply_threshold = 80
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=80))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert (await _reload(session, tx)).category_source == "ai"


async def test_classifier_end_to_end_postgres(postgres_sessions, monkeypatch, categorization_enqueue):
    """Locks (FOR UPDATE, advisory), JSON detail and the bulk inserts on Postgres."""
    from app.models.account import Account
    from tests.test_admin_service import _make_user

    async with postgres_sessions() as session:
        user = await _make_user(session, f"clf_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.flush()
        ws = account.workspace_id
        food = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=ws, name="Food")
        fun = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=ws, name="Fun")
        session.add_all([food, fun])
        session.add(LlmConnection(
            id=uuid.uuid4(), user_id=user.id, name="a", kind="anthropic",
            api_key_encrypted=encrypt("sk-ant-test"), is_default=True, extra={},
        ))
        await session.flush()
        ifood = _tx(session, user, account, "IFOOD *REST 8812")
        netflix = _tx(session, user, account, "NETFLIX.COM")
        await session.commit()
        ids = {"ifood": ifood.id, "netflix": netflix.id}

    def decide(item, short):
        if "IFOOD" in item["s"][0]:
            return out(short["Food"], p=97, n="iFood", r="delivery")
        return out(short["Fun"], p=60, n="Netflix", new={"g": "Assinaturas", "name": "Streaming"})

    monkeypatch.setattr(pipeline, "ai_classifier", classifier.classify_with_claude)
    monkeypatch.setattr(
        classifier, "resolve_client", AsyncMock(return_value=fake_client(answer(decide)))
    )
    result = await categorization_tasks._categorize(postgres_sessions, ws, "manual", None)
    assert result["status"] == "done"
    assert result["stats"]["applied"] == 1 and result["stats"]["suggested"] == 1

    async with postgres_sessions() as session:
        tx = await session.get(Transaction, ids["ifood"])
        assert tx is not None and (tx.category_id, tx.category_source) == (food.id, "ai")
        [payee] = await _all(session, Payee)
        assert (payee.name, payee.source, tx.payee_id) == ("iFood", "ai", payee.id)
        [sug] = await _all(session, CategorizationSuggestion)
        assert sug.transaction_id == ids["netflix"]
        assert sug.new_category == {"group": "Assinaturas", "name": "Streaming"}
        # Batch path: 101 more stores.
        for name in _names(101):
            _tx(session, user, account, name)
        await session.commit()

    client = fake_client()
    monkeypatch.setattr(classifier, "resolve_client", AsyncMock(return_value=client))
    result = await categorization_tasks._categorize(postgres_sessions, ws, "backlog", None)
    assert result["status"] == "processing_batch"
    # A trigger while the batch is pending marks the run instead of starting.
    busy = await categorization_tasks._categorize(postgres_sessions, ws, "ingest", None)
    assert busy == {"status": "skipped", "reason": "busy"}
    async with postgres_sessions() as session:
        run = await session.get(CategorizationRun, uuid.UUID(result["run_id"]))
        assert run is not None and run.detail["rerun"] is True and "batch" in run.detail
    requests = client.messages.batches.create.await_args.kwargs["requests"]
    entries = []
    for request in requests:
        short = shorts(request["params"])
        decided = [{"k": i["k"], **out(short["Fun"], p=99)} for i in sent_items(request["params"])]
        entries.append(SimpleNamespace(
            custom_id=request["custom_id"],
            result=SimpleNamespace(type="succeeded", message=message(decided)),
        ))
    monkeypatch.setattr(
        classifier, "resolve_client", AsyncMock(return_value=fake_client(results=entries))
    )

    assert await categorization_tasks._poll(postgres_sessions) == {
        "collected": 1, "pending": 0, "failed": 0,
    }
    categorization_enqueue.assert_called_once_with(ws, "ingest")
    async with postgres_sessions() as session:
        run = await session.get(CategorizationRun, uuid.UUID(result["run_id"]))
        assert run is not None and run.status == "done" and run.detail == {}
        assert run.stats["applied"] == 101
        ai_rows = await session.scalar(
            select(func.count()).select_from(Transaction).where(
                Transaction.workspace_id == ws, Transaction.category_source == "ai"
            )
        )
        assert ai_rows == 102


def test_ai_named_payee_does_not_change_the_store_key():
    from app.services.categorization.merchant_key import merchant_key

    plain = merchant_key("IFOOD *REST 8812")
    assert merchant_key("IFOOD *REST 8812", payee_name="iFood", payee_source="ai") == plain
    assert merchant_key("IFOOD *REST 8812", payee_name="iFood", payee_source="manual") == "P:IFOOD"


def test_sync_path_is_one_call():
    """`classify` commits right after its paid call assuming nothing else is
    pending, which holds only while the synchronous path is a single call."""
    assert classifier.SYNC_LIMIT <= classifier.CHUNK_SIZE


def test_every_custom_id_is_valid_for_message_batches():
    from app.services.categorization.classifier import _chunk_position, batch_custom_id

    run = CategorizationRun(id=uuid.uuid4())
    for position in (0, 1, 99, 10_000):
        custom_id = batch_custom_id(run, position)
        assert CUSTOM_ID_RE.fullmatch(custom_id), custom_id
        assert _chunk_position(custom_id, run, 10_001) == position
    assert _chunk_position(batch_custom_id(run, 5), run, 5) is None
    assert _chunk_position(f"{uuid.uuid4().hex}-0", run, 5) is None
    assert _chunk_position(f"{run.id}:0", run, 5) is None


@pytest.mark.asyncio
async def test_blank_reason_without_category_is_undecided_and_not_resent(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    tx = _tx(session, test_user, test_account, "LOJA SEM MOTIVO")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: {"n": None, "r": "", "c": None, "alt": None, "p": 10, "new": None})))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    [sug] = await _all(session, CategorizationSuggestion)
    assert (sug.transaction_id, sug.category_id, sug.reason) == (tx.id, None, UNDECIDED_REASON)

    client = wire(fake_client(answer(lambda item, ids: out(ids["Lazer"]))))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    client.messages.create.assert_not_awaited()
    assert len(await _all(session, CategorizationSuggestion)) == 1


@pytest.mark.asyncio
async def test_suggestion_with_null_reason_still_blocks(
    session, test_user, test_workspace, test_account, cats
):
    from app.services.categorization.classifier import _waiting

    tx = _tx(session, test_user, test_account, "LOJA NULA")
    await session.flush()
    session.add(CategorizationSuggestion(
        id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=tx.id,
        merchant_key="LOJA NULA", status="pending", reason=None,
    ))
    await session.commit()

    assert await collect_candidates(session, test_workspace.id, revisit_no_ai=True) == []
    assert await _waiting(session, test_workspace.id, [tx.id]) == {tx.id}


@pytest.mark.asyncio
async def test_undone_ai_decision_goes_to_review_not_back_to_claude(
    client, auth_headers, session, test_user, test_workspace, test_account, cats,
    ai_connection, wire,
):
    from app.services.categorization.review import UNDONE_REASON

    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=97, n="iFood"))))
    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    await session.commit()
    assert (await _reload(session, tx)).category_source == "ai"

    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}
    resp = await client.post(
        "/api/categorization/undo", headers=headers, json={"run_id": str(run.id)}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored"] == 1

    tx = await _reload(session, tx)
    assert (tx.category_id, tx.category_source, tx.payee_id) == (None, "none", None)
    [memory] = await _all(session, MerchantMemory)
    assert memory.ai_category_id is None
    [sug] = await _all(session, CategorizationSuggestion)
    assert (sug.status, sug.category_id, sug.alt_category_id, sug.reason) == (
        "pending", None, cats["Alimentação"], UNDONE_REASON,
    )

    claude = wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=97))))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    await session.commit()
    claude.messages.create.assert_not_awaited()
    assert (await _reload(session, tx)).category_source == "none"

    queue = (await client.get("/api/categorization/review", headers=headers)).json()
    [group] = queue["groups"]
    assert group["suggestion"]["alt_category_id"] == str(cats["Alimentação"])


@pytest.mark.asyncio
async def test_paid_usage_survives_a_failure_while_applying(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, monkeypatch
):
    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    spent = usage(1000, 200)
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"]), use=spent)))

    async def boom(*args, **kwargs):
        raise RuntimeError("apply failed")

    monkeypatch.setattr(classifier, "apply_results", boom)
    prepared = await pipeline.prepare_run(session, test_workspace.id, trigger="manual", use_ai=True)
    await session.commit()
    with pytest.raises(RuntimeError):
        await pipeline.complete_run(session, test_workspace.id, prepared, use_ai=True)
    await session.rollback()

    run = await session.get(CategorizationRun, prepared.run.id, populate_existing=True)
    assert run is not None
    assert run.cost_usd == cost_usd(CLASSIFIER_MODEL, spent, batch=False)
    assert run.input_tokens == 1000


@pytest.mark.asyncio
async def test_cap_counts_batches_in_flight(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    from app.services.categorization.classifier import committed_spend

    session.add(CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="backlog",
        status="processing_batch", batch_id="b0", stats={},
        detail={"batch": {"estimated_cost_usd": "0.999990"}},
    ))
    session.add(CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="organize", trigger="manual",
        status="done", cost_usd=Decimal("0.000005"), stats={}, detail={},
    ))
    await session.commit()
    assert await committed_spend(session, test_workspace.id) == Decimal("0.999995")

    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    client = wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"]))))
    prepared = await pipeline.prepare_run(session, test_workspace.id, trigger="manual", use_ai=True)
    run = await pipeline.complete_run(session, test_workspace.id, prepared, use_ai=True)

    client.messages.create.assert_not_awaited()
    assert run.status == "capped"


@pytest.mark.asyncio
async def test_submitted_batch_stores_its_estimate(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, monkeypatch
):
    for name in _names(120):
        _tx(session, test_user, test_account, name)
    await session.commit()
    monkeypatch.setattr(classifier, "estimate_cost", lambda params, *, batch: Decimal("0.10"))
    wire(fake_client())

    run = await run_categorization(session, test_workspace.id, trigger="backlog", use_ai=True)

    assert run.detail["batch"]["estimated_cost_usd"] == "0.20"
    from app.services.categorization.classifier import committed_spend

    await session.commit()
    assert await committed_spend(session, test_workspace.id) == Decimal("0.20")


@pytest.mark.asyncio
async def test_unusable_answer_is_logged_without_contents(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, caplog
):
    _tx(session, test_user, test_account, "LOJA SEGREDO")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Lazer"], r="segredo"), stop_reason="max_tokens")))

    with caplog.at_level("WARNING", logger="app.services.categorization.classifier"):
        run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    [record] = [r for r in caplog.records if "stop_reason" in r.getMessage()]
    text = record.getMessage()
    assert record.levelname == "WARNING"
    assert str(run.id) in text and "chunk 0" in text and "max_tokens" in text
    assert "SEGREDO" not in text.upper()


@pytest.mark.asyncio
async def test_ai_payee_is_not_created_when_no_row_takes_it(
    session, test_user, test_workspace, test_account, cats
):
    # The model agrees with what a person chose meanwhile: the decision
    # stands, but no row changes, so no payee is created for nobody.
    tx = _tx(
        session, test_user, test_account, "IFOOD *REST 8812",
        source="user", category_id=cats["Alimentação"],
    )
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="manual",
        status="running", stats={}, detail={},
    )
    session.add(run)
    await session.commit()
    group = pipeline.MerchantGroup(
        key="IFOOD", tx_ids=[tx.id], samples=[], count=1, median_amount=Decimal(0),
        type="debit", recurring=False, account_type=None,
    )
    index = build_category_index(await _all(session, Category), [])

    stats = await apply_results(
        session, test_workspace.id, run, {"i1": group},
        [{"k": "i1", **out(index.to_short(cats["Alimentação"]), n="iFood")}],
        index=index, threshold=90, model=CLASSIFIER_MODEL,
    )

    assert stats.applied == 0
    assert await _all(session, Payee) == []


@pytest.mark.asyncio
async def test_confirmed_provider_category_is_a_decision_and_not_paid_again(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    """Claude agreeing with the bank's guess settles the row as `ai` (the
    event records the confirmation), so forgetting the cached decision
    (a tree change) does not send the row to Claude again."""
    from app.services.categorization.memory import forget_ai_decisions

    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812",
             source="provider", category_id=cats["Alimentação"])
    await session.commit()
    first = wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=96))))
    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    await session.commit()

    first.messages.create.assert_awaited_once()
    tx = await _reload(session, tx)
    assert (tx.category_id, tx.category_source) == (cats["Alimentação"], "ai")
    [event] = await _all(session, CategorizationEvent)
    assert (event.previous_source, event.previous_category_id, event.new_category_id) == (
        "provider", cats["Alimentação"], cats["Alimentação"],
    )
    assert (event.source, event.confidence, event.run_id) == ("ai", 96, run.id)

    await forget_ai_decisions(session, test_workspace.id)
    await session.commit()
    again = wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=96))))
    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)
    await session.commit()
    again.messages.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifier_counts_confirmations_apart(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    """Claude agreeing with a guess's category settles it as `ai`, counted
    as `confirmed`: only real changes are `applied`."""
    blank = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    guessed = _tx(session, test_user, test_account, "IFOOD*CLUB 4410",
                  source="provider", category_id=cats["Alimentação"])
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=96))))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert (await _reload(session, blank)).category_source == "ai"
    assert (await _reload(session, guessed)).category_source == "ai"
    assert (run.stats["applied"], run.stats["confirmed"], run.stats["skipped"]) == (1, 1, 0)


async def _clear(session, tx, user):
    """The person empties the row's category (R27: `none`, the event names them)."""
    from app.services.categorization.events import record_change

    await session.flush()
    await record_change(session, tx, source="user", new_category_id=None, user_id=user.id)


@pytest.mark.asyncio
async def test_classifier_only_suggests_for_a_row_the_person_cleared(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    """R27: at or above the threshold the store's other rows are applied,
    but a row the person cleared gets a pending suggestion with the answer."""
    blank = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    cleared = _tx(session, test_user, test_account, "IFOOD*CLUB 4410",
                  source="user", category_id=cats["Lazer"])
    await _clear(session, cleared, test_user)
    await session.commit()
    wire(fake_client(answer(
        lambda item, ids: out(ids["Alimentação"], p=96, r="delivery", n="iFood")
    )))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    blank = await _reload(session, blank)
    cleared = await _reload(session, cleared)
    assert (blank.category_id, blank.category_source) == (cats["Alimentação"], "ai")
    assert (cleared.category_id, cleared.category_source, cleared.payee_id) == (None, "none", None)
    [sug] = await _all(session, CategorizationSuggestion)
    assert (sug.transaction_id, sug.status, sug.category_id) == (
        cleared.id, "pending", cats["Alimentação"],
    )
    assert (sug.confidence, sug.reason, sug.clean_name, sug.run_id) == (96, "delivery", "iFood", run.id)
    assert (run.stats["applied"], run.stats["suggested"], run.stats["skipped"]) == (1, 1, 0)


def _groups(count):
    return {
        name: pipeline.MerchantGroup(
            key=name, tx_ids=[uuid.uuid4()], samples=[name], count=1,
            median_amount=Decimal("10"), type="debit", recurring=False, account_type=None,
        )
        for name in _names(count)
    }


async def _pg_redistribution(postgres_sessions, status):
    from tests.test_categorization_models import _seed_pg

    async with postgres_sessions() as setup:
        ws, category, _txn = await _seed_pg(setup)
        run = CategorizationRun(
            id=uuid.uuid4(), workspace_id=ws, kind="redistribute", trigger="manual",
            status=status, stats={}, detail={},
        )
        setup.add(run)
        await setup.commit()
        return ws, category.id, run.id


@pytest.mark.asyncio
async def test_sending_a_redistribution_batch_does_not_hold_the_workspace(postgres_sessions):
    """The batch is created (a network call) without the workspace lock:
    asking for a classification meanwhile does not wait for the API."""
    import asyncio

    from app.services.categorization import review

    ws, category_id, run_id = await _pg_redistribution(postgres_sessions, "running")
    sending, release = asyncio.Event(), asyncio.Event()
    client = fake_client()

    async def create(**_):
        sending.set()
        await release.wait()
        return SimpleNamespace(id="batch_slow")

    client.messages.batches.create = AsyncMock(side_effect=create)

    async def send():
        async with postgres_sessions() as session:
            run = await session.get(CategorizationRun, run_id)
            assert run is not None
            await classifier.classify(
                session, ws, _groups(120), run, client=client,
                allowed_category_ids={category_id},
            )
            await session.commit()

    sender = asyncio.ensure_future(send())
    try:
        await asyncio.wait_for(sending.wait(), timeout=10)
        async with postgres_sessions() as session:
            queued, created = await asyncio.wait_for(review.request_run(session, ws), timeout=5)
            await session.commit()
        assert created is True and queued.kind == "classify"
    finally:
        release.set()
        await sender
    async with postgres_sessions() as session:
        run = await session.get(CategorizationRun, run_id)
        assert run is not None and (run.status, run.batch_id) == ("processing_batch", "batch_slow")


@pytest.mark.asyncio
async def test_reading_a_batch_does_not_hold_the_workspace(postgres_sessions, monkeypatch):
    import asyncio

    from app.services.categorization import review

    ws, category_id, run_id = await _pg_redistribution(postgres_sessions, "processing_batch")
    async with postgres_sessions() as session:
        run = await session.get(CategorizationRun, run_id)
        assert run is not None
        run.batch_id = "batch_slow"
        run.detail = {"batch": {
            "chunks": [], "rows": {}, "index": [], "allowed": [str(category_id)],
            "capped": False, "estimated_cost_usd": "0",
        }}
        await session.commit()
    reading, release = asyncio.Event(), asyncio.Event()

    async def results(_id):
        reading.set()
        await release.wait()
        for entry in ():
            yield entry

    client = fake_client(status="ended")
    client.messages.batches.results = AsyncMock(side_effect=results)
    monkeypatch.setattr(classifier, "resolve_client", AsyncMock(return_value=client))

    async def collect():
        async with postgres_sessions() as session:
            outcome = await classifier.collect_batch(session, run_id)
            await session.commit()
            return outcome

    collector = asyncio.ensure_future(collect())
    try:
        await asyncio.wait_for(reading.wait(), timeout=10)
        async with postgres_sessions() as session:
            _queued, created = await asyncio.wait_for(review.request_run(session, ws), timeout=5)
            await session.commit()
        assert created is True
    finally:
        release.set()
        outcome = await collector
    assert outcome == "collected"


# --- committed spend and paid batches ---------------------------------------------


def _spend_run(workspace_id, status, *, days_ago=0, cost="0", estimate=None, kind="classify"):
    return CategorizationRun(
        id=uuid.uuid4(), workspace_id=workspace_id, kind=kind, trigger="backlog",
        status=status, cost_usd=Decimal(cost), stats={},
        batch_id="b-old" if status == "processing_batch" else None,
        detail={"batch": {"estimated_cost_usd": estimate}} if estimate is not None else {},
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


@pytest.mark.asyncio
async def test_committed_spend_is_one_statement(session, test_workspace):
    from sqlalchemy import event

    session.add(_spend_run(test_workspace.id, "done", cost="0.10"))
    session.add(_spend_run(test_workspace.id, "processing_batch", estimate="0.05"))
    await session.commit()
    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if "categorization_runs" in statement:
            statements.append(statement)

    sync_engine = session.bind.sync_engine
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        spent = await classifier.committed_spend(session, test_workspace.id)
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)

    assert spent == Decimal("0.15")
    assert len(statements) == 1


@pytest.mark.asyncio
async def test_committed_spend_counts_month_cost_and_inflight_estimates(session, test_workspace):
    ws = test_workspace.id
    session.add_all([
        _spend_run(ws, "done", cost="0.10"),
        # Sent last month and still in flight: its estimate is committed money.
        _spend_run(ws, "processing_batch", days_ago=40, estimate="0.05"),
        # Paid last month: the cap reset since.
        _spend_run(ws, "done", days_ago=40, cost="1.00"),
        # Not a batch kind: never in flight.
        _spend_run(ws, "processing_batch", days_ago=40, estimate="9", kind="organize"),
    ])
    await session.commit()

    assert await classifier.committed_spend(session, ws) == Decimal("0.15")


def _raise_once(original):
    calls = {"n": 0}

    async def wrapped(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("write failed")
        return await original(*args, **kwargs)

    return wrapped


@pytest.mark.asyncio
async def test_batch_stays_collectable_when_work_after_send_fails(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, monkeypatch
):
    """A batch is paid once it is sent: whatever fails after that, the run
    keeps waiting on it with its estimate, and the beat collects it."""
    from sqlalchemy.ext.asyncio import AsyncSession

    ws = test_workspace.id
    for name in _names(120):
        _tx(session, test_user, test_account, name)
    await session.commit()
    monkeypatch.setattr(classifier, "estimate_cost", lambda params, *, batch: Decimal("0.60"))
    client = wire(fake_client())

    async def boom(*args, **kwargs):
        raise RuntimeError("suggestions failed")

    monkeypatch.setattr(classifier, "suggest_without_ai", boom)

    result = await categorization_tasks._categorize(
        _session_maker(session), ws, "ingest", None
    )

    client.messages.batches.create.assert_awaited_once()
    run_id = uuid.UUID(result["run_id"])
    async with AsyncSession(bind=session.bind) as fresh:
        run = await fresh.get(CategorizationRun, run_id)
        assert run is not None
        assert (run.status, run.batch_id, run.is_batch) == ("processing_batch", "batch_1", True)
        assert run.error == "RuntimeError" and run.finished_at is None
        assert run.detail["batch"]["estimated_cost_usd"] == "0.60"
        assert run.detail["batch"]["capped"] is True
        assert await classifier.committed_spend(fresh, ws) == Decimal("0.60")

    wire(fake_client(results=[]))
    summary = await categorization_tasks._poll(_session_maker(session))

    assert summary == {"collected": 1, "pending": 0, "failed": 0}
    run = await session.get(CategorizationRun, run_id, populate_existing=True)
    assert run is not None and run.status == "capped" and run.finished_at is not None
    # Collected: the failure it waited through is no longer its state.
    assert run.error is None


@pytest.mark.asyncio
async def test_batch_tracked_when_writing_tracking_fails_once(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, monkeypatch
):
    ws = test_workspace.id
    for name in _names(120):
        _tx(session, test_user, test_account, name)
    await session.commit()
    monkeypatch.setattr(classifier, "estimate_cost", lambda params, *, batch: Decimal("0.10"))
    wire(fake_client())
    prepared = await pipeline.prepare_run(session, ws, trigger="backlog", use_ai=True)
    await session.commit()
    run_id = prepared.run.id
    # A trigger marked the run meanwhile: the fallback write keeps the mark.
    await session.execute(
        update(CategorizationRun)
        .where(CategorizationRun.id == run_id)
        .values(detail={"rerun": True})
    )
    await session.commit()
    monkeypatch.setattr(classifier, "_merge_detail", _raise_once(classifier._merge_detail))
    rollbacks = []
    real_rollback = session.rollback

    async def counting_rollback():
        rollbacks.append(1)
        await real_rollback()

    monkeypatch.setattr(session, "rollback", counting_rollback)

    with pytest.raises(RuntimeError, match="write failed"):
        await pipeline.complete_run(session, ws, prepared, use_ai=True)
    # Only the savepoint was lost: the fallback wrote in the same
    # transaction, still holding the spend lock.
    assert rollbacks == []
    await session.rollback()

    run = await session.get(CategorizationRun, run_id, populate_existing=True)
    assert run is not None
    assert (run.status, run.batch_id, run.is_batch, run.model) == (
        "processing_batch", "batch_1", True, CLASSIFIER_MODEL,
    )
    assert run.detail["rerun"] is True
    assert run.detail["batch"]["estimated_cost_usd"] == "0.20"
    assert len(run.detail["batch"]["chunks"]) == 2
    assert await classifier.committed_spend(session, ws) == Decimal("0.20")


@pytest.mark.asyncio
async def test_batch_that_cannot_be_tracked_at_all_is_logged(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire, monkeypatch,
    caplog,
):
    ws = test_workspace.id
    for name in _names(120):
        _tx(session, test_user, test_account, name)
    await session.commit()
    wire(fake_client())
    prepared = await pipeline.prepare_run(session, ws, trigger="backlog", use_ai=True)
    await session.commit()
    run_id = prepared.run.id

    async def boom(*args, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(classifier, "_merge_detail", boom)
    monkeypatch.setattr(classifier, "_track_batch", boom)

    with caplog.at_level("ERROR", logger=classifier.logger.name):
        with pytest.raises(RuntimeError, match="database gone"):
            await pipeline.complete_run(session, ws, prepared, use_ai=True)

    assert f"{run_id}: batch batch_1 was sent but could not be tracked" in caplog.text
    await session.rollback()


@pytest.mark.asyncio
async def test_concurrent_batch_submissions_respect_the_cap_postgres(
    postgres_sessions, monkeypatch
):
    """The spend lock's reason to exist: two runs of one workspace sending a
    batch at once, with room for one, send one; the other ends capped."""
    import asyncio

    from app.services.categorization.prompts import CategoryIndex

    ws, category_id, redistribute_id = await _pg_redistribution(postgres_sessions, "running")
    async with postgres_sessions() as setup:
        classify_run = CategorizationRun(
            id=uuid.uuid4(), workspace_id=ws, kind="classify", trigger="backlog",
            status="running", stats={}, detail={},
        )
        setup.add(classify_run)
        await setup.commit()
    monkeypatch.setattr(classifier, "estimate_cost", lambda params, *, batch: Decimal("0.60"))
    client = fake_client()
    sent = itertools.count()

    async def create(**_):
        # Slow enough that both submitters are inside `_submit_batch` at once.
        await asyncio.sleep(0.2)
        return SimpleNamespace(id=f"batch_{next(sent)}")

    client.messages.batches.create = AsyncMock(side_effect=create)
    group = next(iter(_groups(1).values()))
    plan = classifier._Plan(
        groups_by_item={"i1": group}, params={"model": CLASSIFIER_MODEL, "max_tokens": 10},
    )

    async def submit(run_id, allowed):
        async with postgres_sessions() as session:
            run = await session.get(CategorizationRun, run_id)
            assert run is not None
            await classifier._submit_batch(
                session, ws, run, [plan], {}, client=client, index=CategoryIndex(()),
                cap=Decimal("1.00"), allowed_category_ids=allowed,
            )
            await session.commit()

    await asyncio.wait_for(asyncio.gather(
        submit(classify_run.id, None), submit(redistribute_id, {category_id}),
    ), timeout=20)

    client.messages.batches.create.assert_awaited_once()
    async with postgres_sessions() as session:
        runs = (
            await session.execute(
                select(CategorizationRun).where(CategorizationRun.workspace_id == ws)
            )
        ).scalars().all()
        assert sorted(run.status for run in runs) == ["capped", "processing_batch"]
        assert await classifier.committed_spend(session, ws) == Decimal("0.60")


# --- fixed lock: only description-keyed stores apply on their own ---------------------


@pytest.mark.asyncio
async def test_person_transfer_is_always_a_suggestion_even_at_99(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    tx = _tx(session, test_user, test_account, "Transferência enviada pelo Pix|Maria Souza Lima")
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Lazer"], p=99, n="Maria Souza"))))

    run = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    tx = await _reload(session, tx)
    assert (tx.category_id, tx.category_source) == (None, "none")
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.merchant_key.startswith("PIX:")
    assert (sug.status, sug.category_id, sug.confidence) == ("pending", cats["Lazer"], 99)
    assert run.stats["applied"] == 0 and run.stats["suggested"] == 1
    assert await _all(session, CategorizationEvent) == []
    # The cached decision stays empty too: the next run must not apply it either.
    assert await _all(session, MerchantMemory) == []


@pytest.mark.asyncio
async def test_payee_keyed_store_is_always_a_suggestion_at_95(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    payee = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Padaria Estrela", source="sync",
    )
    session.add(payee)
    await session.flush()
    tx = _tx(session, test_user, test_account, "PADARIA ESTRELA 22", payee_id=payee.id)
    await session.commit()
    wire(fake_client(answer(lambda item, ids: out(ids["Alimentação"], p=95))))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    tx = await _reload(session, tx)
    assert (tx.category_id, tx.category_source) == (None, "none")
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.merchant_key.startswith("P:") and sug.status == "pending"


@pytest.mark.asyncio
async def test_description_keyed_store_applies_at_95_and_waits_at_80(
    session, test_user, test_workspace, test_account, cats, ai_connection, wire
):
    sure = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    unsure = _tx(session, test_user, test_account, "CINEMARK 33")
    await session.commit()
    wire(fake_client(answer(
        lambda item, ids: out(ids["Alimentação"], p=95)
        if "IFOOD" in item["s"][0]
        else out(ids["Lazer"], p=80)
    )))

    await run_categorization(session, test_workspace.id, trigger="manual", use_ai=True)

    assert (await _reload(session, sure)).category_source == "ai"
    assert (await _reload(session, unsure)).category_source == "none"
    [sug] = await _all(session, CategorizationSuggestion)
    assert sug.transaction_id == unsure.id
