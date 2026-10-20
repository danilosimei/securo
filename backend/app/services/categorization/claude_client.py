"""Thin async layer over the Anthropic SDK: messages, Message Batches, the key.

The key is the workspace owner's Anthropic connection (`find_ai_connection`),
decrypted here and handed to the SDK; it is never logged. Calls do not
stream (classifier calls are small) and the SDK retries transient failures
twice. SDK failures surface as the agents' `LLMError` family so callers
catch one thing:

- `LLMAuthError`: bad/forbidden key (401/403), not retryable
- `LLMRateLimitError`: 429 after the SDK's retries, retryable
- `LLMUnavailableError`: connection, timeout, 5xx/overloaded, retryable
- `LLMError`: any other API error (e.g. 400), not retryable
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import Message
from anthropic.types.messages.batch_create_params import Request

from app.agents.providers.base import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMUnavailableError,
)
from app.agents.services.crypto import decrypt
from app.models.categorization import CategorizationSettings
from app.services.categorization.settings import find_ai_connection
from sqlalchemy.ext.asyncio import AsyncSession

MAX_RETRIES = 2


async def resolve_client(
    session: AsyncSession, settings: CategorizationSettings
) -> AsyncAnthropic | None:
    """A client on the owner's Anthropic key, or None when there is no usable one."""
    connection = await find_ai_connection(session, settings)
    if connection is None:
        return None
    api_key = decrypt(connection.api_key_encrypted)
    if not api_key:
        return None
    return AsyncAnthropic(
        api_key=api_key,
        base_url=connection.base_url or None,
        max_retries=MAX_RETRIES,
    )


def _translate(exc: anthropic.APIError) -> LLMError:
    """Map an SDK error to the LLMError family (the message never carries the key)."""
    message = str(exc)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMAuthError(message, status=getattr(exc, "status_code", None))
    if isinstance(exc, anthropic.RateLimitError):
        return LLMRateLimitError(message, status=exc.status_code)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return LLMUnavailableError(message, status=exc.status_code)
        return LLMError(message, status=exc.status_code)
    # Connection errors and timeouts.
    return LLMUnavailableError(message)


async def create_message(client: AsyncAnthropic, params: dict[str, Any]) -> Message:
    """One non-streaming Messages call; raises `LLMError` subclasses."""
    try:
        return await client.messages.create(**params)
    except anthropic.APIError as exc:
        raise _translate(exc) from exc


async def create_batch(client: AsyncAnthropic, requests: list[Request]) -> str:
    """Submit a Message Batch; returns its id."""
    try:
        batch = await client.messages.batches.create(requests=requests)
    except anthropic.APIError as exc:
        raise _translate(exc) from exc
    return batch.id


async def batch_status(client: AsyncAnthropic, batch_id: str) -> str:
    """`processing_status`: in_progress, canceling or ended."""
    try:
        batch = await client.messages.batches.retrieve(batch_id)
    except anthropic.APIError as exc:
        raise _translate(exc) from exc
    return batch.processing_status


async def batch_results(
    client: AsyncAnthropic, batch_id: str
) -> AsyncIterator[tuple[str, Message | None, str | None]]:
    """(custom_id, message, error_type) per request; order is not guaranteed.

    A succeeded request has a message; any other outcome has the error type
    (`errored` carries the API error's type, e.g. "overloaded_error";
    "canceled"/"expired" are the result type itself).
    """
    try:
        decoder = await client.messages.batches.results(batch_id)
        async for entry in decoder:
            result = entry.result
            if result.type == "succeeded":
                yield entry.custom_id, result.message, None
            elif result.type == "errored":
                yield entry.custom_id, None, result.error.error.type
            else:
                yield entry.custom_id, None, result.type
    except anthropic.APIError as exc:
        raise _translate(exc) from exc
