"""The conversation with the AI about one store: one person message, one answer.

The one place a request calls Claude while the person waits (spec §1): the
person asked and is looking at the dialog. The conversation lives in the
client, which re-sends it with each message, so the limits are checked
here on what it sends:

    last turn not the person's        -> InvalidConversation (a ValueError)
    > MAX_PERSON_MESSAGES of theirs   -> ConversationLimit
    AI off / no key                   -> Unavailable("disabled" | "no_connection")
    store context + params
    month's spend + estimate > cap    -> Unavailable("capped"), nothing sent
    commit (ends the reads' transaction: no connection or lock is held
    while the model answers)
    one Messages call, TIMEOUT_SECONDS, no retries
      -> a `CategorizationRun(kind="ask")` with what it cost, committed on
         its own (shielded from cancellation) as soon as the call returns:
         a timeout, a cancellation (the person went away) or any unexpected
         error after sending books the estimate, since the call may have
         been billed; an API error a failed run at no cost and `ModelError`
         (a refused key: `Unavailable("no_connection")`)
      -> the answer, a suggestion with how many rows it applies to

The rate per workspace (`RATE_LIMIT`) is the API's. The monthly cap is
checked without the spend lock: holding it through the call would
serialize the workspace's messages and stall its batches, and the most it
lets through is one estimate per message in flight (fractions of a cent,
bounded by the rate). The answer never writes anything but its run: no
cached AI decision (`MerchantMemory.ai_*`), no suggestion, no event --
accepting it goes through `choose`. Unlike the rest of the package this
module commits, twice (before the call, and the run), on purpose: the
brief's "flush only" would hold a transaction open through the call and
let a lost request take a paid call out of the ledger. Nothing logs the
conversation, the answer or the key.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.providers.base import LLMAuthError, LLMError
from app.models.categorization import (
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.services import category_service
from app.services.categorization import creation, memory, review
from app.services.categorization.ask_prompts import (
    ASK_MODEL,
    StoreContext,
    ask_params,
    build_ask_schema,
    build_ask_system_blocks,
    conversation_messages,
    parse_answer,
)
from app.services.categorization.classifier import (
    account_usage,
    category_index,
    committed_spend,
    estimate_cost,
)
from app.services.categorization.claude_client import create_message, resolve_client
from app.services.categorization.merchant_key import PERSON_PREFIX
from app.services.categorization.prompts import build_examples, prompt_sample
from app.services.categorization.settings import get_settings

#: The person's messages in one conversation (the client starts a new one).
MAX_PERSON_MESSAGES = 30
#: Each answer, end to end; the SDK does not retry within it.
TIMEOUT_SECONDS = 15
#: (messages, seconds) per workspace, counted by the API.
RATE_LIMIT = (20, 60)
#: Example descriptions of the store sent as context.
SAMPLES = 3
#: What the person sees when the answer cannot be used.
FALLBACK_REPLY = "Não consegui responder agora; tente dizer de outro jeito."
#: `error` of a run whose call did not answer in time.
TIMEOUT_ERROR = "timeout"
_COMPACT = (",", ":")


class InvalidConversation(ValueError):
    """The conversation does not end with the person's message."""


class AskError(Exception):
    """Why a message got no answer; `code` is what the API reports."""

    code: str = "ask_error"

    def __init__(self, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(self.code)


class Unavailable(AskError):
    """The AI cannot be asked now: `disabled`, `no_connection` or `capped`."""


class ConversationLimit(AskError):
    code = "conversation_limit"


class AskTimeout(AskError):
    code = "timeout"


class ModelError(AskError):
    code = "model_error"


@dataclass
class AskSuggestion:
    category_id: uuid.UUID | None
    #: {"group", "name"} when the category does not exist yet.
    new_category: dict[str, str] | None
    #: Whether a visible group of that name exists (always true for an
    #: existing category).
    group_exists: bool
    confidence: int
    #: Rows an Accept would file: those the context was built from.
    applies_to: int


@dataclass
class AskAnswer:
    reply: str
    suggestion: AskSuggestion | None
    #: The store's whole note, updated; None leaves the saved one as it is.
    note: str | None
    #: The answer as the client sends it back as the `assistant` turn.
    assistant_turn: str
    person_messages_left: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chunks(ids: list[uuid.UUID]):
    for start in range(0, len(ids), memory.CHUNK):
        yield ids[start:start + memory.CHUNK]


async def _pending_rows(
    session: AsyncSession, workspace_id: uuid.UUID, key: str
) -> list[uuid.UUID]:
    """The store's rows with a pending suggestion nobody overrode by hand
    since (what Revisar shows and an Accept files)."""
    result = await session.execute(
        select(CategorizationSuggestion.transaction_id)
        .join(Transaction, review.open_row(workspace_id))
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
            CategorizationSuggestion.merchant_key == memory.stored_key(key),
        )
        .order_by(CategorizationSuggestion.created_at.desc(), CategorizationSuggestion.id)
    )
    return list(dict.fromkeys(result.scalars().all()))


async def _suggestions_of(
    session: AsyncSession, workspace_id: uuid.UUID, tx_ids: list[uuid.UUID]
) -> list[CategorizationSuggestion]:
    """Every suggestion over `tx_ids`, newest first."""
    found: list[CategorizationSuggestion] = []
    for chunk in _chunks(tx_ids):
        result = await session.execute(
            select(CategorizationSuggestion).where(
                CategorizationSuggestion.workspace_id == workspace_id,
                CategorizationSuggestion.transaction_id.in_(chunk),
            )
        )
        found.extend(result.scalars().all())
    found.sort(
        key=lambda s: (s.created_at.timestamp() if s.created_at else 0.0, s.id.hex), reverse=True
    )
    return found


def _store_name(
    key: str, suggestions: Sequence[CategorizationSuggestion], hit: memory.MemoryHit
) -> str | None:
    """The clean name of the latest suggestion made for this store (a row may
    have been suggested under another key before), the memory's, then the
    readable key (none for a key that names nobody)."""
    stored = memory.stored_key(key)
    for sug in suggestions:
        if sug.merchant_key == stored and sug.clean_name and sug.clean_name.strip():
            return sug.clean_name.strip()
    if hit.ai_clean_name and hit.ai_clean_name.strip():
        return hit.ai_clean_name.strip()
    return review.key_name(key)


def _samples(rows: Sequence[Transaction]) -> tuple[str, ...]:
    counts = Counter(prompt_sample(tx.description) for tx in rows)
    return tuple(text for text, _ in counts.most_common() if text)[:SAMPLES]


def _most_common_category(rows: Sequence[Transaction]) -> uuid.UUID | None:
    counts = Counter(tx.category_id for tx in rows if tx.category_id is not None)
    return counts.most_common(1)[0][0] if counts else None


async def store_context(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    merchant_key: str,
    transaction_ids: list[uuid.UUID] | None,
) -> tuple[StoreContext, list[Transaction]]:
    """What the backend knows about the store, and the rows it is about.

    With `transaction_ids`, those rows of the workspace (Aplicadas); without,
    the store's pending rows (Revisar). No row is `review.NotFound`.
    """
    if transaction_ids is not None:
        ids = list(dict.fromkeys(transaction_ids))
    else:
        ids = await _pending_rows(session, workspace_id, merchant_key)
    rows = await review.load_transactions(session, workspace_id, ids) if ids else []
    if not rows:
        raise review.NotFound("Nenhuma transação desta loja")
    # Newest first, so ties (most common category, samples) are stable.
    rows.sort(key=lambda tx: (tx.date, tx.id.hex), reverse=True)

    suggestions = await _suggestions_of(session, workspace_id, [tx.id for tx in rows])
    latest_pending = next((s for s in suggestions if s.status == "pending"), None)
    hit = (await memory.lookup(session, workspace_id, [merchant_key]))[merchant_key]
    history = tuple(
        sorted(
            ((uuid.UUID(raw), count) for raw, count in hit.distribution.items() if count > 0),
            key=lambda entry: (-entry[1], str(entry[0])),
        )
    )
    total = Decimal(0)
    for tx in rows:
        amount = tx.amount_primary if tx.amount_primary is not None else tx.amount
        total += abs(Decimal(amount or 0))
    credits = sum(1 for tx in rows if tx.type == "credit")
    last: date | None = max(tx.date for tx in rows)
    ctx = StoreContext(
        name=_store_name(merchant_key, suggestions, hit),
        is_person=merchant_key.startswith(PERSON_PREFIX),
        count=len(rows),
        total=total,
        last_date=last,
        samples=_samples(rows),
        kind="c" if credits * 2 > len(rows) else "d",
        current_category_id=_most_common_category(rows),
        suggested_category_id=latest_pending.category_id if latest_pending else None,
        alt_category_id=latest_pending.alt_category_id if latest_pending else None,
        history=history,
        note=hit.note,
    )
    return ctx, rows


async def _resolve_new(
    session: AsyncSession, workspace_id: uuid.UUID, group: str, name: str
) -> tuple[uuid.UUID | None, bool]:
    """(existing assignable category of that name in a visible group of that
    name, whether such a group exists): names compared as `creation` does."""
    wanted_group, wanted_name = creation.norm_name(group), creation.norm_name(name)
    groups = [
        g.id
        for g in (
            await session.execute(
                select(CategoryGroup).where(
                    CategoryGroup.workspace_id == workspace_id,
                    CategoryGroup.is_hidden.is_(False),
                )
            )
        ).scalars().all()
        if creation.norm_name(g.name) == wanted_group
    ]
    if not groups:
        return None, False
    assignable = await category_service.get_assignable_category_ids(session, workspace_id)
    categories = (
        await session.execute(
            select(Category)
            .where(Category.workspace_id == workspace_id, Category.group_id.in_(groups))
            .order_by(Category.name, Category.id)
        )
    ).scalars().all()
    for category in categories:
        if category.id in assignable and creation.norm_name(category.name) == wanted_name:
            return category.id, True
    return None, True


def _fallback_turn() -> str:
    return json.dumps(
        {"reply": FALLBACK_REPLY, "suggestion": None, "note": None},
        ensure_ascii=False,
        separators=_COMPACT,
    )


async def _book(
    session: AsyncSession,
    run: CategorizationRun,
    *,
    status: str,
    error: str | None = None,
    cost: Decimal | None = None,
) -> None:
    """Close `run` and commit it on its own, shielded: a paid call must reach
    the ledger even when the request is being cancelled."""
    run.status = status
    run.error = error
    run.finished_at = _now()
    if cost is not None:
        run.cost_usd = cost
    session.add(run)
    await asyncio.shield(session.commit())


def _timed_out(exc: BaseException) -> bool:
    """A timeout, ours or the SDK's (which surfaces as `LLMUnavailableError`)."""
    return isinstance(exc, TimeoutError) or isinstance(exc.__cause__, anthropic.APITimeoutError)


async def ask(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    merchant_key: str,
    transaction_ids: list[uuid.UUID] | None,
    turns: list[tuple[str, str]],
) -> AskAnswer:
    """Answer the person's last message about the store (see the module doc)."""
    if not turns or turns[-1][0] != "user":
        raise InvalidConversation("A última mensagem da conversa precisa ser da pessoa")
    person_messages = sum(1 for role, _ in turns if role == "user")
    if person_messages > MAX_PERSON_MESSAGES:
        raise ConversationLimit()
    settings = await get_settings(session, workspace_id)
    if not settings.enabled:
        raise Unavailable("disabled")
    client = await resolve_client(session, settings)
    if client is None:
        raise Unavailable("no_connection")
    try:
        index = await category_index(session, workspace_id)
        memory_rows = (
            await session.execute(
                select(MerchantMemory).where(MerchantMemory.workspace_id == workspace_id)
            )
        ).scalars().all()
        ctx, rows = await store_context(session, workspace_id, merchant_key, transaction_ids)
        params = ask_params(
            build_ask_system_blocks(index, build_examples(memory_rows, index), ctx),
            conversation_messages(turns),
            build_ask_schema(index),
        )
        estimate = estimate_cost(params, batch=False)
        if await committed_spend(session, workspace_id) + estimate > Decimal(
            settings.monthly_cap_usd
        ):
            raise Unavailable("capped")

        run = CategorizationRun(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            kind="ask",
            trigger="manual",
            status="running",
            model=ASK_MODEL,
            cost_usd=Decimal(0),
            stats={},
            detail={},
        )
        # Only reads (and maybe the default settings row, worth keeping) so
        # far: end the transaction before the network.
        await session.commit()
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                message = await create_message(
                    client.with_options(timeout=TIMEOUT_SECONDS, max_retries=0), params
                )
        except (TimeoutError, LLMError) as exc:
            if _timed_out(exc):
                await _book(session, run, status="failed", error=TIMEOUT_ERROR, cost=estimate)
                raise AskTimeout() from exc
            await _book(session, run, status="failed", error=type(exc).__name__)
            if isinstance(exc, LLMAuthError):
                raise Unavailable("no_connection") from exc
            raise ModelError() from exc
        except BaseException as exc:
            # Cancelled or failed after sending: the call may have been billed.
            await _book(session, run, status="failed", error=type(exc).__name__, cost=estimate)
            raise
        account_usage(run, getattr(message, "usage", None), batch=False)
        await _book(session, run, status="done")
    finally:
        await client.close()

    left = MAX_PERSON_MESSAGES - person_messages
    parsed = parse_answer(message, index)
    if parsed is None:
        return AskAnswer(
            reply=FALLBACK_REPLY,
            suggestion=None,
            note=None,
            assistant_turn=_fallback_turn(),
            person_messages_left=left,
        )
    suggestion: AskSuggestion | None = None
    if parsed.category_id is not None:
        suggestion = AskSuggestion(
            category_id=parsed.category_id,
            new_category=None,
            group_exists=True,
            confidence=parsed.confidence,
            applies_to=len(rows),
        )
    elif parsed.new_category is not None:
        group, name = parsed.new_category
        existing, group_exists = await _resolve_new(session, workspace_id, group, name)
        suggestion = AskSuggestion(
            category_id=existing,
            new_category=None if existing is not None else {"group": group, "name": name},
            group_exists=group_exists,
            confidence=parsed.confidence,
            applies_to=len(rows),
        )
    return AskAnswer(
        reply=parsed.reply,
        suggestion=suggestion,
        note=parsed.note,
        assistant_turn=parsed.raw,
        person_messages_left=left,
    )
