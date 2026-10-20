import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2
import pytest
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from app.agents.models.connection import LlmConnection
from app.agents.providers.base import LLMAuthError, LLMRateLimitError, LLMUnavailableError
from app.agents.services.crypto import encrypt
from app.services.categorization import costs
from app.services.categorization.claude_client import (
    batch_results,
    batch_status,
    create_batch,
    create_message,
    resolve_client,
)
from app.services.categorization.costs import cost_usd
from app.services.categorization.settings import get_settings

M = 1_000_000


def usage(i=0, o=0, cw=0, cr=0):
    return SimpleNamespace(
        input_tokens=i, output_tokens=o,
        cache_creation_input_tokens=cw, cache_read_input_tokens=cr,
    )


def test_cost_usd_haiku_batch_halves():
    assert cost_usd("claude-haiku-5-5", usage(M, M), batch=True) == Decimal("0.300000")
    assert cost_usd("claude-haiku-5-5", usage(M, M), batch=False) == Decimal("0.600000")


def test_cost_usd_counts_cache_read_and_write():
    # Haiku: write 1.25 x 0.10, read 0.01
    assert cost_usd("claude-haiku-5-5", usage(cw=M, cr=M), batch=False) == Decimal("0.135000")
    # Opus: read 0.20, write 1.25 x 4.00
    assert cost_usd("claude-opus-5-5", usage(cw=M, cr=M), batch=False) == Decimal("5.200000")
    assert cost_usd("claude-opus-5-5", usage(M, M), batch=False) == Decimal("24.000000")


def test_cost_usd_missing_attrs_and_unknown_model(caplog):
    assert cost_usd("claude-haiku-5-5", SimpleNamespace(input_tokens=M, output_tokens=None), batch=False) == Decimal("0.100000")
    with caplog.at_level("WARNING"):
        assert cost_usd("claude-nope", usage(M, M), batch=False) == Decimal(0)
    assert "claude-nope" in caplog.text


@pytest.mark.asyncio
async def test_resolve_client_none_without_connection(session, test_workspace):
    settings = await get_settings(session, test_workspace.id)
    assert await resolve_client(session, settings) is None


@pytest.mark.asyncio
async def test_resolve_client_ignores_non_anthropic_connections(session, test_user, test_workspace):
    settings = await get_settings(session, test_workspace.id)
    session.add(LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="o", kind="openai",
        api_key_encrypted=encrypt("sk-openai"), is_default=True, extra={},
    ))
    await session.flush()
    assert await resolve_client(session, settings) is None


@pytest.mark.asyncio
async def test_resolve_client_builds_client_with_decrypted_key(session, test_user, test_workspace):
    settings = await get_settings(session, test_workspace.id)
    session.add(LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="a", kind="anthropic",
        api_key_encrypted=encrypt("sk-ant-secret"), is_default=True, extra={},
    ))
    await session.flush()
    client = await resolve_client(session, settings)
    assert isinstance(client, anthropic.AsyncAnthropic)
    assert client.api_key == "sk-ant-secret"
    assert client.max_retries == 2


@pytest.mark.asyncio
async def test_resolve_client_none_without_key(session, test_user, test_workspace):
    settings = await get_settings(session, test_workspace.id)
    session.add(LlmConnection(
        id=uuid.uuid4(), user_id=test_user.id, name="a", kind="anthropic",
        api_key_encrypted=None, is_default=True, extra={},
    ))
    await session.flush()
    assert await resolve_client(session, settings) is None


@pytest.mark.asyncio
async def test_create_message_passes_params():
    client = MagicMock()
    client.messages.create = AsyncMock(return_value="msg")
    assert await create_message(client, {"model": "m", "max_tokens": 10, "messages": []}) == "msg"
    client.messages.create.assert_awaited_once_with(model="m", max_tokens=10, messages=[])


def _status_error(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=req), body=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc, expected",
    [
        (_status_error(anthropic.AuthenticationError, 401), LLMAuthError),
        (_status_error(anthropic.PermissionDeniedError, 403), LLMAuthError),
        (_status_error(anthropic.RateLimitError, 429), LLMRateLimitError),
        (_status_error(anthropic.InternalServerError, 500), LLMUnavailableError),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), LLMUnavailableError),
    ],
)
async def test_create_message_maps_sdk_errors(exc, expected):
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=exc)
    with pytest.raises(expected):
        await create_message(client, {})


@pytest.mark.asyncio
async def test_batch_roundtrip():
    client = MagicMock()
    client.messages.batches.create = AsyncMock(return_value=SimpleNamespace(id="b1"))
    client.messages.batches.retrieve = AsyncMock(return_value=SimpleNamespace(processing_status="ended"))
    request = Request(
        custom_id="a",
        params=MessageCreateParamsNonStreaming(
            model="claude-haiku-5-5", max_tokens=10, messages=[{"role": "user", "content": "x"}]
        ),
    )
    assert await create_batch(client, [request]) == "b1"
    assert await batch_status(client, "b1") == "ended"
    client.messages.batches.create.assert_awaited_once_with(requests=[request])


@pytest.mark.asyncio
async def test_batch_results_yields_message_or_error_type():
    ok = SimpleNamespace(custom_id="a", result=SimpleNamespace(type="succeeded", message="MSG"))
    err = SimpleNamespace(custom_id="b", result=SimpleNamespace(
        type="errored", error=SimpleNamespace(error=SimpleNamespace(type="overloaded_error"))))
    expired = SimpleNamespace(custom_id="c", result=SimpleNamespace(type="expired"))

    async def decoder():
        for item in (ok, err, expired):
            yield item

    client = MagicMock()
    client.messages.batches.results = AsyncMock(return_value=decoder())
    got = [r async for r in batch_results(client, "b1")]
    assert got == [("a", "MSG", None), ("b", None, "overloaded_error"), ("c", None, "expired")]


def test_prices_cover_both_models():
    assert set(costs.PRICES) == {"claude-haiku-5-5", "claude-opus-5-5"}
