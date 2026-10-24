"""The prompt evaluation script: truth, no leakage, scoring and the cost stop.

The script is loaded by path (it lives in `scripts/`, not in the package). The
LLM client is a fake that records the requests and answers `c1` for everything.
"""
import importlib.util
import sys
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.categorization import CategorizationEvent
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_categorization_prompt.py"
_spec = importlib.util.spec_from_file_location("eval_categorization_prompt", _SCRIPT)
assert _spec and _spec.loader
ev = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ev
_spec.loader.exec_module(ev)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
BEFORE_CUT = NOW - timedelta(days=90)
AFTER_CUT = NOW - timedelta(days=10)


@pytest.fixture
async def tree(session, test_user, test_workspace):
    group = CategoryGroup(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                          name="Lazer", position=0)
    session.add(group)
    await session.flush()
    made = {}
    for name in ("Cinema", "Streaming", "Mercado"):
        cat = Category(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                       name=name, icon="x", color="#000000", group_id=group.id)
        session.add(cat)
        made[name] = cat.id
    await session.commit()
    return made


def _store(session, user, account, description):
    tx = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=account.workspace_id,
        account_id=account.id, description=description, amount=Decimal("25.00"),
        date=date(2026, 9, 10), effective_date=date(2026, 9, 10), type="debit",
        source="sync", status="posted", category_source="user",
    )
    session.add(tx)
    return tx


def _decide(session, workspace_id, tx, category_id, when, user_id=None, **extra):
    fields = dict(source="user", memory_pick=True, run_id=None, undone_at=None)
    fields.update(extra)
    session.add(CategorizationEvent(
        id=uuid.uuid4(), workspace_id=workspace_id, transaction_id=tx.id,
        new_category_id=category_id, created_at=when, user_id=user_id, **fields,
    ))


def fake_client():
    """Records each request; answers `c1` at 95 for every item."""
    client = MagicMock()
    client.close = AsyncMock()

    def create(**params):
        items = json.loads(params["messages"][0]["content"])
        answers = [{"k": i["k"], "n": None, "r": "x", "c": "c1", "alt": None, "p": 95, "new": None}
                   for i in items]
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({"items": answers}))],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )

    client.messages.create = AsyncMock(side_effect=create)
    return client


async def _run(session, workspace_id, client, **kwargs):
    return await ev.evaluate(session, workspace_id, client=client, now=NOW, **kwargs)


@pytest.mark.asyncio
async def test_choice_after_the_cut_never_becomes_context(
    session, test_user, test_workspace, test_account, tree
):
    ws = test_workspace.id
    early = _store(session, test_user, test_account, "CINEMARK 33")
    late = _store(session, test_user, test_account, "NETFLIX.COM")
    await session.flush()
    _decide(session, ws, early, tree["Cinema"], BEFORE_CUT, test_user.id)
    _decide(session, ws, early, tree["Cinema"], AFTER_CUT, test_user.id)  # the truth
    _decide(session, ws, late, tree["Streaming"], AFTER_CUT, test_user.id)  # the truth
    await session.commit()
    client = fake_client()

    result = await _run(session, ws, client)

    # Both stores are asked about (neither is settled by memory before the cut).
    assert set(result.truth) == {"CINEMARK", "NETFLIX.COM"}
    old, new = (call.kwargs["system"][1]["text"] for call in client.messages.create.await_args_list)
    for context in (old, new):
        assert "NETFLIX" not in context  # chosen only after the cut
        assert "CINEMARK -> c1" in context  # chosen before the cut too: a valid example


@pytest.mark.asyncio
async def test_truth_is_the_latest_deliberate_choice_per_store(
    session, test_user, test_workspace, test_account, tree
):
    ws = test_workspace.id
    store = _store(session, test_user, test_account, "NETFLIX.COM")
    other = _store(session, test_user, test_account, "CINEMARK 33")
    organized = _store(session, test_user, test_account, "MERCADO CENTRAL")
    nameless = _store(session, test_user, test_account, "")
    await session.flush()
    _decide(session, ws, store, tree["Cinema"], AFTER_CUT - timedelta(days=5), test_user.id)
    _decide(session, ws, store, tree["Streaming"], AFTER_CUT, test_user.id)
    # Not the person's deliberate choices: organizer run, no person, derived, undone, cleared.
    _decide(session, ws, other, tree["Mercado"], AFTER_CUT, test_user.id, run_id=None, memory_pick=False)
    _decide(session, ws, other, tree["Mercado"], AFTER_CUT, None)
    _decide(session, ws, other, tree["Mercado"], AFTER_CUT, test_user.id, undone_at=NOW)
    _decide(session, ws, nameless, tree["Mercado"], AFTER_CUT, test_user.id)
    run = await _make_run(session, ws)
    _decide(session, ws, organized, tree["Mercado"], AFTER_CUT, test_user.id, run_id=run)
    await session.commit()

    decisions = await ev.load_decisions(session, ws, since=NOW - timedelta(days=60))
    assert ev.latest_per_store(decisions) == {"NETFLIX.COM": tree["Streaming"]}


async def _make_run(session, workspace_id):
    from app.models.categorization import CategorizationRun

    run = CategorizationRun(id=uuid.uuid4(), workspace_id=workspace_id, kind="organize",
                            trigger="manual", status="done")
    session.add(run)
    await session.flush()
    return run.id


@pytest.mark.asyncio
async def test_descriptions_reach_only_the_new_prompt(
    session, test_user, test_workspace, test_account, tree, tmp_path
):
    ws = test_workspace.id
    store = _store(session, test_user, test_account, "NETFLIX.COM")
    await session.flush()
    _decide(session, ws, store, tree["Streaming"], AFTER_CUT, test_user.id)
    await session.commit()
    path = tmp_path / "d.json"
    path.write_text(json.dumps([["lazer", "STREAMING", "Assinaturas de vídeo."]]), encoding="utf8")
    client = fake_client()

    result = await _run(session, ws, client, descriptions=ev.load_descriptions(path))

    old, new = (call.kwargs["system"][1]["text"] for call in client.messages.create.await_args_list)
    assert "Assinaturas de vídeo." in new and "Assinaturas de vídeo." not in old
    assert result.unmatched == set()


def test_score_counts_hits_by_kind_and_confidence_band():
    cat_a, cat_b = uuid.uuid4(), uuid.uuid4()
    truth = {"NETFLIX": cat_a, "P:PADARIA": cat_a, "PIX:MARIA": cat_b, "CINEMARK": cat_b}
    answers = {
        "NETFLIX": ev.Answer(cat_a, 95),
        "P:PADARIA": ev.Answer(cat_b, 85),
        "PIX:MARIA": ev.Answer(cat_b, 60),
        # CINEMARK: the model skipped it
    }
    total, by_kind, by_band = ev.score(truth, answers)
    assert (total.right, total.total) == (2, 4)
    assert (by_kind["descrição"].right, by_kind["descrição"].total) == (1, 2)
    assert (by_kind["P:"].right, by_kind["P:"].total) == (0, 1)
    assert (by_kind["PIX:"].right, by_kind["PIX:"].total) == (1, 1)
    assert (by_band["90+"].right, by_band["90+"].total) == (1, 1)
    assert (by_band["80-89"].right, by_band["80-89"].total) == (0, 1)
    assert (by_band["<70"].right, by_band["<70"].total) == (1, 2)  # 60 and the skipped one
    text = ev.format_report(truth, answers, answers)
    assert "50.0% (2/4)" in text and "antigo" in text and "novo" in text


@pytest.mark.asyncio
async def test_estimate_above_the_limit_aborts_before_any_call(
    session, test_user, test_workspace, test_account, tree
):
    ws = test_workspace.id
    store = _store(session, test_user, test_account, "NETFLIX.COM")
    await session.flush()
    _decide(session, ws, store, tree["Streaming"], AFTER_CUT, test_user.id)
    await session.commit()
    client = fake_client()

    with pytest.raises(ev.CostAbort):
        await _run(session, ws, client, max_usd=Decimal("0.000001"))

    client.messages.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_builds_and_prices_without_calling_or_writing(
    session, test_user, test_workspace, test_account, tree
):
    ws = test_workspace.id
    store = _store(session, test_user, test_account, "NETFLIX.COM")
    await session.flush()
    _decide(session, ws, store, tree["Streaming"], AFTER_CUT, test_user.id)
    await session.commit()
    client = fake_client()
    result = await _run(session, ws, client, dry_run=True)

    client.messages.create.assert_not_awaited()
    assert result.estimate > 0 and result.truth == {"NETFLIX.COM": tree["Streaming"]}
    assert not session.dirty and not session.new
