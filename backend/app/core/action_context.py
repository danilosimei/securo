"""The action a request is part of, so its category changes group together.

One click (save a row, apply to the similar ones, answer a store in
Revisar, undo) is one action in Categorization > Histórico, however many
rows it touches. A screen that wants to undo or link to what it just did
names the action itself with the `X-Action-Id` header; otherwise each
request gets a fresh one. Outside a request (a worker, the CLI) there is
none, and the events module falls back to the run or the batch.
"""

import uuid
from contextvars import ContextVar
from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send

HEADER = b"x-action-id"

_action: ContextVar[Optional[uuid.UUID]] = ContextVar("categorization_action", default=None)


def current_action() -> Optional[uuid.UUID]:
    return _action.get()


def _from_header(scope: Scope) -> uuid.UUID:
    for name, value in scope.get("headers", ()):
        if name == HEADER:
            try:
                return uuid.UUID(value.decode("latin-1"))
            except ValueError:
                break
    return uuid.uuid4()


class ActionMiddleware:
    """Sets the request's action for everything the request runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _action.set(_from_header(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _action.reset(token)
