"""POST /api/categorization/ask: the conversation with the AI about one store."""
import asyncio
import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.schemas.category import CategoryCreate
from app.schemas.category_group import CategoryGroupCreate
from app.services import category_group_service, category_service
from app.services.categorization import ask, classifier, costs
from app.services.categorization.settings import get_settings
from tests.test_categorization_api import API, _suggest, _tx


class _FakeClient:
    def __init__(self):
        self.closed = False
        self.options = None

    def with_options(self, **options):
        self.options = options
        return self

    async def close(self):
        self.closed = True


def _usage():
    return SimpleNamespace(
        input_tokens=900, output_tokens=60,
        cache_creation_input_tokens=0, cache_read_input_tokens=800,
    )


def _message(answer):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(answer))],
        stop_reason="end_turn",
        usage=_usage(),
    )


@pytest.fixture
def fake_ai(monkeypatch):
    """A client that always resolves and a model that answers `state.answer`."""
    state = SimpleNamespace(
        calls=[], answer={"reply": "Ok.", "suggestion": None, "note": None},
        client=_FakeClient(), delay=0.0, error=None, hook=None,
    )

    async def resolve(session, settings):
        return state.client

    async def create(client, params):
        state.calls.append(params)
        if state.hook is not None:
            state.hook()
        if state.delay:
            await asyncio.sleep(state.delay)
        if state.error is not None:
            raise state.error
        return _message(state.answer)

    monkeypatch.setattr(ask, "resolve_client", resolve)
    monkeypatch.setattr(ask, "create_message", create)
    return state


async def _tree(session, user, workspace):
    group = await category_group_service.create_group(
        session, workspace.id, user.id, CategoryGroupCreate(name="Casa")
    )
    jardim = await category_service.create_category(
        session, workspace.id, user.id, CategoryCreate(name="Jardim", group_id=group.id)
    )
    mercado = await category_service.create_category(
        session, workspace.id, user.id, CategoryCreate(name="Mercado", group_id=group.id)
    )
    return {"Jardim": jardim.id, "Mercado": mercado.id}


async def _pending(session, user, account, description, key, n=3, **sug):
    txs = [_tx(session, user, account, description) for _ in range(n)]
    await session.flush()
    for tx in txs:
        _suggest(session, tx, key, **sug)
    await session.commit()
    return txs


def _turns(person_messages):
    turns = []
    for i in range(person_messages):
        if i:
            turns.append({"role": "assistant", "content": f"resposta {i}"})
        turns.append({"role": "user", "content": f"pergunta {i}"})
    return turns


async def _ask(client, headers, body, status=200):
    resp = await client.post(f"{API}/ask", headers=headers, json=body)
    assert resp.status_code == status, resp.text
    return resp.json()


def _body(key, text="É o jardineiro", **extra):
    return {"merchant_key": key, "messages": [{"role": "user", "content": text}], **extra}


async def _runs(session, workspace_id):
    return list((await session.execute(
        select(CategorizationRun).where(
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.kind == "ask",
        ).execution_options(populate_existing=True)
    )).scalars().all())


@pytest.mark.asyncio
async def test_ask_returns_reply_and_suggestion_with_applies_to(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _tree(session, test_user, test_workspace)
    await _pending(session, test_user, test_account, "AURINO SILVA", "PIX:AURINO SILVA")
    index = await classifier.category_index(session, test_workspace.id)
    fake_ai.answer = {
        "reply": "Então é Casa › Jardim.",
        "suggestion": {"c": "c1", "new": None, "p": 88},
        "note": "Aurino: jardineiro semanal",
    }

    body = await _ask(client, auth_headers, _body("PIX:AURINO SILVA"))

    assert body["reply"] == "Então é Casa › Jardim."
    assert body["suggestion"] == {
        "category_id": str(index.from_short("c1")),
        "new_category": None,
        "group_exists": True,
        "confidence": 88,
        "applies_to": 3,
    }
    assert body["note"] == "Aurino: jardineiro semanal"
    assert body["person_messages_left"] == ask.MAX_PERSON_MESSAGES - 1
    assert json.loads(body["assistant_turn"])["reply"] == "Então é Casa › Jardim."
    assert fake_ai.client.options == {"timeout": ask.TIMEOUT_SECONDS, "max_retries": 0}
    assert fake_ai.client.closed is True
    params = fake_ai.calls[0]
    assert params["model"] == "claude-haiku-5-5"
    assert params["thinking"] == {"type": "disabled"}
    assert params["messages"] == [{"role": "user", "content": "É o jardineiro"}]
    store = params["system"][2]["text"]
    assert '"q":3' in store
    assert "Aurino Silva" in store


@pytest.mark.asyncio
async def test_ask_from_applied_rows_applies_to_those_rows(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    tree = await _tree(session, test_user, test_workspace)
    rows = [
        _tx(session, test_user, test_account, "HORTIFRUTI BOM", source="ai",
            category_id=tree["Mercado"], amount="20.00", day=date(2026, 9, d))
        for d in (1, 2)
    ]
    _tx(session, test_user, test_account, "HORTIFRUTI BOM", source="ai", category_id=tree["Mercado"])
    await session.commit()
    index = await classifier.category_index(session, test_workspace.id)
    fake_ai.answer = {
        "reply": "Mercado mesmo.", "suggestion": {"c": index.to_short(tree["Mercado"]), "new": None, "p": 90},
        "note": None,
    }

    body = await _ask(client, auth_headers, _body(
        "HORTIFRUTI BOM", transaction_ids=[str(tx.id) for tx in rows],
    ))

    assert body["suggestion"]["applies_to"] == 2
    assert body["suggestion"]["category_id"] == str(tree["Mercado"])
    store = fake_ai.calls[0]["system"][2]["text"]
    assert '"q":2' in store
    assert '"v":40' in store
    assert f'"atual":"{index.to_short(tree["Mercado"])}"' in store
    assert '"u":"2026-09-02"' in store


@pytest.mark.asyncio
async def test_ask_unknown_store_is_404(client, auth_headers, test_workspace, fake_ai):
    body = await _ask(client, auth_headers, _body("NADA AQUI"), status=404)
    assert body["detail"]["code"] == "not_found"
    assert fake_ai.calls == []


@pytest.mark.asyncio
async def test_ask_never_writes_memory_or_suggestions(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    tree = await _tree(session, test_user, test_workspace)
    await _pending(session, test_user, test_account, "AURINO SILVA", "PIX:AURINO SILVA", n=2,
                   category_id=tree["Mercado"])
    session.add(MerchantMemory(
        id=uuid.uuid4(), workspace_id=test_workspace.id, merchant_key="PIX:AURINO SILVA",
        counts={str(tree["Mercado"]): 1}, ai_category_id=tree["Mercado"], ai_confidence=60,
        ai_clean_name="Aurino", note="jardineiro",
    ))
    await session.commit()
    index = await classifier.category_index(session, test_workspace.id)
    fake_ai.answer = {
        "reply": "Jardim.", "suggestion": {"c": index.to_short(tree["Jardim"]), "new": None, "p": 95},
        "note": "Aurino: jardineiro semanal",
    }

    async def snapshot():
        mem = (await session.execute(
            select(MerchantMemory).where(MerchantMemory.workspace_id == test_workspace.id)
            .execution_options(populate_existing=True)
        )).scalars().all()
        sugs = (await session.execute(
            select(CategorizationSuggestion.id, CategorizationSuggestion.status,
                   CategorizationSuggestion.category_id)
            .where(CategorizationSuggestion.workspace_id == test_workspace.id)
            .order_by(CategorizationSuggestion.id)
        )).all()
        events = await session.scalar(select(func.count(CategorizationEvent.id)).where(
            CategorizationEvent.workspace_id == test_workspace.id
        ))
        return (
            [(m.merchant_key, m.counts, m.ai_category_id, m.ai_confidence, m.ai_clean_name, m.note)
             for m in mem],
            [tuple(s) for s in sugs],
            events,
        )

    before = await snapshot()
    body = await _ask(client, auth_headers, _body("PIX:AURINO SILVA"))
    assert body["note"] == "Aurino: jardineiro semanal"
    assert await snapshot() == before
    # The saved note and the cached name reach the model.
    store = fake_ai.calls[0]["system"][2]["text"]
    assert '"nota":"jardineiro"' in store


@pytest.mark.asyncio
async def test_ask_records_cost_run(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    await _ask(client, auth_headers, _body("PADARIA"))

    runs = await _runs(session, test_workspace.id)
    assert len(runs) == 1
    run = runs[0]
    assert (run.kind, run.trigger, run.status, run.model) == ("ask", "manual", "done", "claude-haiku-5-5")
    assert run.finished_at is not None
    assert (run.input_tokens, run.output_tokens, run.cache_read_tokens) == (900, 60, 800)
    assert run.cost_usd > 0
    assert await costs.month_spend(session, test_workspace.id) == run.cost_usd


@pytest.mark.asyncio
async def test_ask_30th_person_message_is_allowed_and_31st_is_409(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)

    body = await _ask(client, auth_headers, {"merchant_key": "PADARIA", "messages": _turns(30)})
    assert body["person_messages_left"] == 0

    body = await _ask(
        client, auth_headers, {"merchant_key": "PADARIA", "messages": _turns(31)}, status=409
    )
    assert body["detail"]["code"] == "conversation_limit"
    assert body["detail"]["message"]
    assert len(fake_ai.calls) == 1


@pytest.mark.asyncio
async def test_ask_capped_is_409_without_calling_model(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    settings = await get_settings(session, test_workspace.id)
    settings.monthly_cap_usd = Decimal("0.00")
    await session.commit()

    body = await _ask(client, auth_headers, _body("PADARIA"), status=409)

    assert body["detail"]["code"] == "capped"
    assert fake_ai.calls == []
    assert await _runs(session, test_workspace.id) == []
    assert fake_ai.client.closed is True


@pytest.mark.asyncio
async def test_ask_disabled_is_503(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    settings = await get_settings(session, test_workspace.id)
    settings.enabled = False
    await session.commit()

    body = await _ask(client, auth_headers, _body("PADARIA"), status=503)

    assert body["detail"]["code"] == "disabled"
    assert fake_ai.calls == []


@pytest.mark.asyncio
async def test_ask_without_connection_is_503(
    client, auth_headers, session, test_user, test_account, test_workspace, monkeypatch
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)

    async def no_client(session, settings):
        return None

    monkeypatch.setattr(ask, "resolve_client", no_client)
    body = await _ask(client, auth_headers, _body("PADARIA"), status=503)
    assert body["detail"]["code"] == "no_connection"


@pytest.mark.asyncio
async def test_ask_timeout_is_504_and_books_estimate(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai, monkeypatch
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    monkeypatch.setattr(ask, "TIMEOUT_SECONDS", 0.01)
    fake_ai.delay = 0.1

    body = await _ask(client, auth_headers, _body("PADARIA"), status=504)

    assert body["detail"]["code"] == "timeout"
    runs = await _runs(session, test_workspace.id)
    assert len(runs) == 1
    assert (runs[0].status, runs[0].error) == ("failed", "timeout")
    assert runs[0].cost_usd == classifier.estimate_cost(fake_ai.calls[0], batch=False)
    assert runs[0].cost_usd > 0
    assert fake_ai.client.closed is True


@pytest.mark.asyncio
async def test_ask_model_error_is_502_and_books_failed_run(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    from app.agents.providers.base import LLMRateLimitError

    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    fake_ai.error = LLMRateLimitError("slow down", status=429)

    body = await _ask(client, auth_headers, _body("PADARIA"), status=502)

    assert body["detail"]["code"] == "model_error"
    runs = await _runs(session, test_workspace.id)
    assert [(r.status, r.error, r.cost_usd) for r in runs] == [
        ("failed", "LLMRateLimitError", Decimal("0"))
    ]


@pytest.mark.asyncio
async def test_ask_sdk_timeout_is_504(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    import anthropic
    import httpx2

    from app.agents.providers.base import LLMUnavailableError

    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    error = LLMUnavailableError("Request timed out.")
    error.__cause__ = anthropic.APITimeoutError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake_ai.error = error

    body = await _ask(client, auth_headers, _body("PADARIA"), status=504)

    assert body["detail"]["code"] == "timeout"
    runs = await _runs(session, test_workspace.id)
    assert [(r.status, r.error) for r in runs] == [("failed", "timeout")]


@pytest.mark.asyncio
async def test_ask_refused_key_is_503_no_connection(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    from app.agents.providers.base import LLMAuthError

    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    fake_ai.error = LLMAuthError("bad key", status=401)

    body = await _ask(client, auth_headers, _body("PADARIA"), status=503)
    assert body["detail"]["code"] == "no_connection"


@pytest.mark.asyncio
async def test_ask_unusable_answer_is_a_fixed_reply(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    fake_ai.answer = {"reply": "", "suggestion": None, "note": None}

    body = await _ask(client, auth_headers, _body("PADARIA"))

    assert body["reply"] == ask.FALLBACK_REPLY
    assert body["suggestion"] is None
    assert body["note"] is None
    assert json.loads(body["assistant_turn"])["reply"] == ask.FALLBACK_REPLY
    assert len(await _runs(session, test_workspace.id)) == 1


@pytest.mark.asyncio
async def test_ask_sends_no_merchant_key_or_documents(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    key = "PIX:#3f9a1c0d2b7e"
    await _pending(session, test_user, test_account, "PIX ENVIADO 123.456.789-09", key, n=2)

    await _ask(client, auth_headers, _body(key, text="meu cpf é 123.456.789-09, ag 1234 cc 98765-4"))

    sent = json.dumps(fake_ai.calls[0], ensure_ascii=False)
    assert key not in sent
    assert "3f9a1c0d2b7e" not in sent
    assert "123.456.789-09" not in sent
    assert "12345678909" not in sent
    assert "98765" not in sent


@pytest.mark.asyncio
async def test_ask_new_category_matching_existing_becomes_category_id(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    group = await category_group_service.create_group(
        session, test_workspace.id, test_user.id, CategoryGroupCreate(name="Saúde")
    )
    farmacia = await category_service.create_category(
        session, test_workspace.id, test_user.id, CategoryCreate(name="Farmácia", group_id=group.id),
    )
    await _pending(session, test_user, test_account, "DROGARIA Y", "DROGARIA", n=2)

    fake_ai.answer = {
        "reply": "Farmácia.", "suggestion": {"c": None, "new": {"g": "saude", "name": "FARMACIA"}, "p": 80},
        "note": None,
    }
    body = await _ask(client, auth_headers, _body("DROGARIA"))
    assert body["suggestion"]["category_id"] == str(farmacia.id)
    assert body["suggestion"]["new_category"] is None
    assert body["suggestion"]["group_exists"] is True
    assert body["suggestion"]["applies_to"] == 2

    fake_ai.answer = {
        "reply": "Remédios.", "suggestion": {"c": None, "new": {"g": "saude", "name": "Remédios"}, "p": 70},
        "note": None,
    }
    body = await _ask(client, auth_headers, _body("DROGARIA"))
    assert body["suggestion"]["category_id"] is None
    assert body["suggestion"]["new_category"] == {"group": "saude", "name": "Remédios"}
    assert body["suggestion"]["group_exists"] is True

    fake_ai.answer = {
        "reply": "Pet.", "suggestion": {"c": None, "new": {"g": "Pets", "name": "Remédios"}, "p": 70},
        "note": None,
    }
    body = await _ask(client, auth_headers, _body("DROGARIA"))
    assert body["suggestion"]["new_category"] == {"group": "Pets", "name": "Remédios"}
    assert body["suggestion"]["group_exists"] is False


@pytest.mark.asyncio
async def test_ask_hidden_group_does_not_count_as_existing(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    group = await category_group_service.create_group(
        session, test_workspace.id, test_user.id, CategoryGroupCreate(name="Saúde")
    )
    await category_service.create_category(
        session, test_workspace.id, test_user.id, CategoryCreate(name="Farmácia", group_id=group.id),
    )
    group.is_hidden = True
    await session.commit()
    await _pending(session, test_user, test_account, "DROGARIA Y", "DROGARIA", n=1)
    fake_ai.answer = {
        "reply": "Farmácia.", "suggestion": {"c": None, "new": {"g": "Saúde", "name": "Farmácia"}, "p": 80},
        "note": None,
    }

    body = await _ask(client, auth_headers, _body("DROGARIA"))

    assert body["suggestion"]["category_id"] is None
    assert body["suggestion"]["new_category"] == {"group": "Saúde", "name": "Farmácia"}
    assert body["suggestion"]["group_exists"] is False


@pytest.mark.asyncio
async def test_ask_last_turn_must_be_person(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    body = await _ask(client, auth_headers, {
        "merchant_key": "PADARIA",
        "messages": [
            {"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"},
        ],
    }, status=422)
    assert body["detail"]["code"] == "invalid_conversation"
    assert fake_ai.calls == []


@pytest.mark.asyncio
async def test_ask_rejects_bad_bodies(client, auth_headers, test_workspace, fake_ai):
    await _ask(client, auth_headers, {"merchant_key": "PADARIA", "messages": []}, status=422)
    await _ask(client, auth_headers, {
        "merchant_key": "PADARIA", "messages": [{"role": "system", "content": "x"}],
    }, status=422)
    await _ask(client, auth_headers, {
        "merchant_key": "PADARIA", "messages": [{"role": "user", "content": ""}],
    }, status=422)
    await _ask(client, auth_headers, {
        "merchant_key": "PADARIA", "messages": [{"role": "user", "content": "x" * 4001}],
    }, status=422)
    await _ask(client, auth_headers, {
        "merchant_key": "PADARIA", "messages": [{"role": "user", "content": "x"}] * 81,
    }, status=422)
    await _ask(client, auth_headers, _body("PADARIA", transaction_ids=[]), status=422)


@pytest.mark.asyncio
async def test_ask_requires_write(client, viewer_auth_headers, fake_ai):
    await _ask(client, viewer_auth_headers, _body("PADARIA"), status=403)
    assert fake_ai.calls == []


@pytest.mark.asyncio
async def test_summary_reports_ai_enabled(client, auth_headers, session, test_workspace):
    assert (await client.get(f"{API}/summary", headers=auth_headers)).json()["ai_enabled"] is True
    settings = await get_settings(session, test_workspace.id)
    settings.enabled = False
    await session.commit()
    assert (await client.get(f"{API}/summary", headers=auth_headers)).json()["ai_enabled"] is False


def test_limits():
    assert ask.MAX_PERSON_MESSAGES == 30
    assert ask.TIMEOUT_SECONDS == 15
    assert ask.RATE_LIMIT == (20, 60)



# --- the call holds no transaction; the run always reaches the ledger ---------


def _service_ask(session, workspace_id, key="PADARIA"):
    return ask.ask(
        session, workspace_id, merchant_key=key, transaction_ids=None, turns=[("user", "oi")],
    )


@pytest.mark.asyncio
async def test_ask_holds_no_transaction_during_the_call(
    session, test_user, test_account, test_workspace, fake_ai
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    seen = []
    fake_ai.hook = lambda: seen.append(session.in_transaction())

    await _service_ask(session, test_workspace.id)

    assert seen == [False]
    await session.rollback()  # the run was committed by the service itself
    assert [r.status for r in await _runs(session, test_workspace.id)] == ["done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError, RuntimeError])
async def test_ask_cancelled_or_unexpected_after_sending_books_estimate(
    session, test_user, test_account, test_workspace, fake_ai, error
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    fake_ai.error = error()

    with pytest.raises(error):
        await _service_ask(session, test_workspace.id)

    await session.rollback()
    runs = await _runs(session, test_workspace.id)
    assert [(r.status, r.error) for r in runs] == [("failed", error.__name__)]
    assert runs[0].cost_usd == classifier.estimate_cost(fake_ai.calls[0], batch=False)
    assert runs[0].cost_usd > 0
    assert fake_ai.client.closed is True


@pytest.mark.asyncio
async def test_ask_failure_after_the_answer_keeps_its_run(
    session, test_user, test_account, test_workspace, fake_ai, monkeypatch
):
    await _pending(session, test_user, test_account, "PADARIA", "PADARIA", n=1)
    fake_ai.answer = {
        "reply": "Nova.", "suggestion": {"c": None, "new": {"g": "Casa", "name": "Pão"}, "p": 70},
        "note": None,
    }

    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ask, "_resolve_new", broken)
    with pytest.raises(RuntimeError):
        await _service_ask(session, test_workspace.id)

    await session.rollback()
    runs = await _runs(session, test_workspace.id)
    assert [r.status for r in runs] == ["done"]
    assert runs[0].cost_usd > 0


@pytest.mark.asyncio
async def test_ask_store_name_only_from_this_stores_suggestions(
    client, auth_headers, session, test_user, test_account, test_workspace, fake_ai
):
    rows = [
        _tx(session, test_user, test_account, "HORTIFRUTI BOM", source="ai") for _ in range(2)
    ]
    await session.flush()
    other = _suggest(session, rows[0], "OUTRA LOJA", clean_name="Outra Loja")
    other.status = "accepted"
    await session.commit()

    await _ask(client, auth_headers, _body(
        "HORTIFRUTI BOM", transaction_ids=[str(tx.id) for tx in rows],
    ))
    store = fake_ai.calls[0]["system"][2]["text"]
    assert "Outra Loja" not in store
    assert "Hortifruti Bom" in store

    mine = _suggest(session, rows[1], "HORTIFRUTI BOM", clean_name="Hortifruti do Bairro")
    mine.status = "accepted"
    await session.commit()
    await _ask(client, auth_headers, _body(
        "HORTIFRUTI BOM", transaction_ids=[str(tx.id) for tx in rows],
    ))
    assert "Hortifruti do Bairro" in fake_ai.calls[1]["system"][2]["text"]
