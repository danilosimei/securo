"""The Claude classifier: the stores the memory could not settle, decided by Claude.

    groups (merchant key -> rows)
      -> chunks of <= 100 stores, sorted by key
      -> monthly cap: month's spend + estimate > cap? -> `capped`, suggestions
      -> <= 100 stores: one Messages call per chunk, applied right away
         >  100 stores: one Message Batch (a request per chunk), the run waits
            in `processing_batch` and `collect_batch` (from `poll_batches`)
            applies it when it ends
      -> per store: p >= threshold -> rows become `ai` (event with confidence,
         reason, model, run; a guess it confirms too) + the decision is
         cached in the memory; a row the person cleared only gets it as a
         suggestion (R27); otherwise -> a pending suggestion (c, alt, p,
         r, clean name, new category); no answer, a refusal or a cut-off
         answer -> a suggestion with no category (`UNDECIDED_REASON`), which
         keeps the rows from being paid for again. A redistribution after a
         split applies any child of the family and never suggests.

The run was committed between the memory phase and this one, so a person
may have touched the rows while Claude answered: every row is re-read
(`FOR UPDATE`) and checked again right before anything is written. Nothing
here commits but what was paid: the usage of a call, and the tracking of
a sent batch (so a failure afterwards cannot hide it from the cap, nor
leave a paid batch uncollected); nothing logs the request, the answer or
the key.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types.messages.batch_create_params import Request
from sqlalchemy import and_, case, or_, select, update
from sqlalchemy import insert as sa_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import get_workspace_timezone
from app.models.categorization import (
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services.categorization import pipeline
from app.services.categorization.claude_client import (
    batch_results,
    batch_status,
    create_batch,
    create_message,
    resolve_client,
)
from app.services.categorization.costs import _month_bounds, cost_usd
from app.services.categorization.events import (
    PROTECTED_SOURCES,
    UNSET,
    StagedChange,
    cleared_by_person,
    stage_change,
    write_staged,
)
from app.services.categorization.memory import lookup, remember_ai_decision, repoint_store
from app.services.categorization.merchant_key import merchant_key, scrub_documents
from app.services.categorization.pipeline import (
    AUTO_APPLY_SOURCES,
    PIPELINE_SOURCES,
    SUPERSEDED_REASON,
    MerchantGroup,
    blocking_suggestion,
    close_stats,
    stored_key,
    suggest_for_run,
    suggest_without_ai,
    supersede_no_ai,
)
from app.services.categorization.prompts import (
    CLASSIFIER_MODEL,
    CategoryIndex,
    build_category_index,
    build_examples,
    build_history,
    build_result_schema,
    build_system_blocks,
    build_user_message,
    classify_params,
)
from app.services.categorization.settings import get_settings
from app.services.category_service import get_assignable_category_ids
from app.services.payee_service import _same_name_as, get_or_create_payee

__all__ = [
    "BATCH_KINDS",
    "SUPERSEDED_REASON",
    "UNDECIDED_REASON",
    "ApplyStats",
    "apply_results",
    "batch_custom_id",
    "batch_estimate",
    "classify",
    "classify_with_claude",
    "collect_batch",
    "committed_spend",
    "estimate_cost",
]

logger = logging.getLogger(__name__)

#: Stores per request.
CHUNK_SIZE = 100
#: Up to this many stores the run calls the Messages API and finishes now;
#: beyond it, one Message Batch (half the price, up to 24 h). Keep it
#: <= `CHUNK_SIZE`: the synchronous path is then one call, and the commit
#: after it (see `classify`) has nothing else pending.
SYNC_LIMIT = 100
#: `reason` of the suggestions for stores the model did not decide.
UNDECIDED_REASON = "IA não decidiu"
#: The answer was refused or cut off: nothing in it can be trusted.
UNDECIDED_STOPS = frozenset({"refusal", "max_tokens"})
REASON_LENGTH = 120
NAME_LENGTH = 255
NEW_CATEGORY_LENGTH = 100
#: Rough characters per token for the cost estimate.
CHARS_PER_TOKEN = 4
#: `run.detail` key holding what `collect_batch` needs to apply a batch.
BATCH_DETAIL = "batch"
#: `run.detail` key of a capped redistribution: the rows no answer covered
#: (never sent, or their request ended without one). Its retry sends only
#: these, so the rows the model left with no category are not paid again.
UNSENT_DETAIL = "unsent_tx_ids"
#: `error` of a redistribution whose batch ended with requests unanswered
#: (not capped): retryable, and the retry sends only `UNSENT_DETAIL`.
INCOMPLETE_ERROR = "batch_incomplete"
#: Ids per `IN (...)`.
ID_CHUNK = 1000
#: Run kinds that may wait on a Message Batch (a redistribution after a
#: split goes through the same path as a classification).
BATCH_KINDS = ("classify", "redistribute")
#: Message Batches accept only `^[a-zA-Z0-9_-]{1,64}$` as `custom_id`:
#: `<run id hex>-<chunk position>`.
CUSTOM_ID_SEPARATOR = "-"


@dataclass
class ApplyStats:
    """Rows changed, guesses settled at the category they held, rows queued
    with a proposal, rows left (moved on since, already right, or undecided)."""

    applied: int = 0
    confirmed: int = 0
    suggested: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class _Answer:
    category_id: uuid.UUID | None
    alt_category_id: uuid.UUID | None
    confidence: int
    reason: str | None
    clean_name: str | None
    new_category: dict[str, str] | None


@dataclass(frozen=True)
class _Plan:
    """One request: its stores by item id, and the call's parameters."""

    groups_by_item: dict[str, MerchantGroup]
    params: dict[str, Any]


# --- sanitizing the answer --------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())[:limit].rstrip()
    return cleaned or None


def _confidence(value: Any) -> int:
    """0..100; anything that is not a number is 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return 0
    else:
        return 0
    if not math.isfinite(number):
        return 0
    return max(0, min(100, round(number)))


def _category(value: Any, index: CategoryIndex, assignable: set[uuid.UUID]) -> uuid.UUID | None:
    """A short id still usable now (the tree may have changed since the call)."""
    category_id = index.from_short(value) if isinstance(value, str) else None
    return category_id if category_id in assignable else None


def _new_category(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    group = _text(value.get("g"), NEW_CATEGORY_LENGTH)
    name = _text(value.get("name"), NEW_CATEGORY_LENGTH)
    if group is None or name is None:
        return None
    return {"group": group, "name": name}


def _sanitize(
    item: Any, index: CategoryIndex, assignable: set[uuid.UUID]
) -> tuple[str, _Answer] | None:
    if not isinstance(item, dict) or not isinstance(item.get("k"), str):
        return None
    category_id = _category(item.get("c"), index, assignable)
    alt = _category(item.get("alt"), index, assignable)
    name = item.get("n")
    return item["k"], _Answer(
        category_id=category_id,
        alt_category_id=alt if alt != category_id else None,
        confidence=_confidence(item.get("p")),
        reason=_text(item.get("r"), REASON_LENGTH),
        clean_name=_text(scrub_documents(name), NAME_LENGTH) if isinstance(name, str) else None,
        new_category=_new_category(item.get("new")),
    )


def _items(message: Any) -> list[Any] | None:
    """The answer's items, or None when it cannot be used."""
    if getattr(message, "stop_reason", None) in UNDECIDED_STOPS:
        return None
    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if getattr(block, "type", "text") != "text" or not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            return None
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else None
    return None


# --- applying ---------------------------------------------------------------------


def _id_chunks(ids: list[uuid.UUID]) -> list[list[uuid.UUID]]:
    return [ids[start:start + ID_CHUNK] for start in range(0, len(ids), ID_CHUNK)]


async def _load_rows(
    session: AsyncSession, workspace_id: uuid.UUID, tx_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Transaction]:
    """The rows as they are now, locked until commit (Postgres)."""
    rows: dict[uuid.UUID, Transaction] = {}
    for ids in _id_chunks(tx_ids):
        result = await session.execute(
            select(Transaction)
            .where(Transaction.workspace_id == workspace_id, Transaction.id.in_(ids))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        rows.update({tx.id: tx for tx in result.scalars().all()})
    return rows


async def _waiting(
    session: AsyncSession, workspace_id: uuid.UUID, tx_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Rows with a pending suggestion other than a model-less one."""
    waiting: set[uuid.UUID] = set()
    for ids in _id_chunks(tx_ids):
        waiting.update(
            (
                await session.execute(
                    select(CategorizationSuggestion.transaction_id).where(
                        CategorizationSuggestion.workspace_id == workspace_id,
                        CategorizationSuggestion.transaction_id.in_(ids),
                        CategorizationSuggestion.status == "pending",
                        blocking_suggestion(),
                    )
                )
            ).scalars().all()
        )
    return waiting


def _revisable(tx: Transaction) -> bool:
    """Still a candidate: counted, unpaired, and its category still a guess."""
    if (
        tx.status != "posted"
        or tx.transfer_pair_id is not None
        or tx.is_ignored
        or tx.source == "opening_balance"
    ):
        return False
    if tx.category_source in AUTO_APPLY_SOURCES:
        return True
    return tx.category_source in PIPELINE_SOURCES and tx.category_id is None


def _redistributable(tx: Transaction, family: set[uuid.UUID]) -> bool:
    """A row of a split category still waiting to be redistributed: counted,
    unpaired, and still in the split's family -- whoever filed it (the
    person approved splitting that category, so `user` rows move too)."""
    return (
        tx.status == "posted"
        and tx.transfer_pair_id is None
        and not tx.is_ignored
        and tx.source != "opening_balance"
        and tx.category_id in family
    )


def _person_disagrees(rows: list[Transaction], category_id: uuid.UUID | None) -> bool:
    """A person filed a row of the store elsewhere while the model answered."""
    return any(
        tx.category_source in PROTECTED_SOURCES and tx.category_id != category_id for tx in rows
    )


def _applied_category(
    answer: _Answer | None,
    rows: list[Transaction],
    threshold: int,
    allowed_category_ids: set[uuid.UUID] | None,
) -> uuid.UUID | None:
    """The category an answer applies to its store's rows, or None (a
    suggestion, or nothing in a redistribution).

    A classification applies at or above `threshold`, unless a person filed
    a row of the store elsewhere meanwhile. A redistribution applies any
    child of the split family: the person approved the split.
    """
    if answer is None or answer.category_id is None:
        return None
    if allowed_category_ids is not None:
        return answer.category_id if answer.category_id in allowed_category_ids else None
    if answer.confidence < threshold or _person_disagrees(rows, answer.category_id):
        return None
    return answer.category_id


def _keeps_key(tx: Transaction, key: str, name: str, source: str) -> bool:
    return merchant_key(tx.description, payee_name=name, payee_source=source) == key


async def _payees_for(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    key: str,
    name: str | None,
    rows: list[Transaction],
) -> dict[uuid.UUID, uuid.UUID]:
    """Row -> payee named after the clean name, for the rows that get one.

    Only rows with no payee: one chosen by hand or set by a rule is never
    replaced (a rule's payee is not recognizable on the row, so any payee
    counts as chosen). A payee of the same name (case-insensitive) is
    reused as it is, never renamed or merged; otherwise one is created with
    source "ai". A payee that would change the row's merchant key (a
    same-name payee typed by hand regroups the row under "P:<name>") is
    left off, so the row stays with the store its memory lives under.
    """
    empty = [tx for tx in rows if tx.payee_id is None]
    if name is None or not empty:
        return {}
    payee = await session.scalar(
        select(Payee).where(Payee.workspace_id == workspace_id, _same_name_as(name)).limit(1)
    )
    if payee is None:
        # Created only when some row will take it (`rows` are the rows about
        # to change).
        if not any(_keeps_key(tx, key, name, "ai") for tx in empty):
            return {}
        payee = await get_or_create_payee(
            session, empty[0].user_id, name, workspace_id=workspace_id, source="ai"
        )
    return {tx.id: payee.id for tx in empty if _keeps_key(tx, key, payee.name, payee.source)}


def _suggestion(
    workspace_id: uuid.UUID,
    run: CategorizationRun,
    key: str,
    tx: Transaction,
    answer: _Answer | None,
    now: datetime,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": workspace_id,
        "transaction_id": tx.id,
        "run_id": run.id,
        "merchant_key": stored_key(key),
        "status": "pending",
        "created_at": now,
    }
    if answer is None:
        row["reason"] = UNDECIDED_REASON
        return row
    row.update(
        clean_name=answer.clean_name,
        category_id=answer.category_id,
        alt_category_id=answer.alt_category_id,
        confidence=answer.confidence,
        # No category and no reason is the model not deciding: say so (and
        # never leave a NULL reason a later filter could misread).
        reason=answer.reason or (UNDECIDED_REASON if answer.category_id is None else None),
    )
    if answer.new_category is not None:
        row["new_category"] = answer.new_category
    return row


async def _insert_suggestions(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Rows with no new-category proposal leave the `new_category` key out, so
    # the column is SQL NULL rather than JSON null; one insert per key set.
    for with_new in (True, False):
        shaped = [row for row in rows if ("new_category" in row) is with_new]
        if shaped:
            await session.execute(sa_insert(CategorizationSuggestion), shaped)


async def apply_results(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run: CategorizationRun,
    groups_by_item: dict[str, MerchantGroup],
    items: Sequence[Any],
    *,
    index: CategoryIndex,
    threshold: int,
    model: str,
    allowed_category_ids: set[uuid.UUID] | None = None,
) -> ApplyStats:
    """Write one answer: `groups_by_item` maps the request's item ids to stores.

    Unknown item ids and malformed items are ignored, an invalid short id
    (or one no longer assignable) is no category, a store missing from the
    answer gets a suggestion with no category. A decision at or above
    `threshold` is applied to the store's rows as `ai`, unless a person
    filed a row of the store elsewhere meanwhile; below it, or without a
    category, the rows get a pending suggestion. Each row is checked again
    as it is now (`_revisable`, no newer pending question); a row decided
    here closes its model-less suggestion. A decision that confirms the
    category a guess (`none`/`provider`) already holds is applied all the
    same: the row becomes `ai` (the event records the confirmation), so it
    is settled and never paid for again. A row the person cleared (R27) is
    never applied: it gets the decision as a pending suggestion.

    With `allowed_category_ids` (a redistribution after a split) the rows
    are the split category's, whatever their origin: a row moves while it
    is still in that family (`_redistributable`), a person's earlier choice
    does not hold the store back, and the store's memory follows the rows
    to the chosen child. The person approved the split, so a child of the
    family is applied whatever the confidence (the event keeps it, and it
    can be undone); no category leaves the rows in the first child, with no
    suggestion: Review hides a person's rows, and a waiting row would be
    skipped by every later redistribution.
    """
    assignable = await get_assignable_category_ids(session, workspace_id)
    answers: dict[str, _Answer] = {}
    for item in items:
        parsed = _sanitize(item, index, assignable)
        if parsed is not None and parsed[0] in groups_by_item:
            answers.setdefault(*parsed)

    tx_ids = list(dict.fromkeys(i for g in groups_by_item.values() for i in g.tx_ids))
    current = await _load_rows(session, workspace_id, tx_ids)
    waiting = await _waiting(session, workspace_id, tx_ids)
    # Checked on the rows as they are now: a person may have cleared one
    # while the model answered. A split family's rows hold a category.
    cleared = (
        await cleared_by_person(session, [
            tx.id for tx in current.values()
            if tx.category_id is None and tx.category_source == "none"
        ])
        if allowed_category_ids is None
        else set()
    )

    stats = ApplyStats()
    staged: list[StagedChange] = []
    suggestions: list[dict[str, Any]] = []
    decided: list[uuid.UUID] = []
    now = datetime.now(timezone.utc)
    for item_id, group in groups_by_item.items():
        rows = [current[i] for i in dict.fromkeys(group.tx_ids) if i in current]
        open_rows = [
            tx for tx in rows
            if (
                _revisable(tx)
                if allowed_category_ids is None
                else _redistributable(tx, allowed_category_ids)
            )
            and tx.id not in waiting
        ]
        stats.skipped += len(set(group.tx_ids)) - len(open_rows)
        answer = answers.get(item_id)
        category_id = _applied_category(answer, rows, threshold, allowed_category_ids)

        if answer is not None and category_id is not None:
            # A guess the model confirms becomes its decision; a row already
            # settled at that category (by the pipeline or a person) keeps
            # its origin; a row the person cleared is only asked about.
            held = [tx for tx in open_rows if tx.id in cleared]
            changing = [
                tx for tx in open_rows
                if tx.id not in cleared
                and (tx.category_id != category_id or tx.category_source in AUTO_APPLY_SOURCES)
            ]
            stats.skipped += len(open_rows) - len(changing) - len(held)
            for tx in held:
                suggestions.append(_suggestion(workspace_id, run, group.key, tx, answer, now))
                decided.append(tx.id)
            stats.suggested += len(held)
            payees = await _payees_for(session, workspace_id, group.key, answer.clean_name, changing)
            for tx in changing:
                change = stage_change(
                    tx,
                    source="ai",
                    new_category_id=category_id,
                    run_id=run.id,
                    reason=answer.reason,
                    confidence=answer.confidence,
                    model=model,
                    new_payee_id=payees.get(tx.id, UNSET),
                )
                if change is not None:
                    staged.append(change)
                    decided.append(tx.id)
                    if change.previous_category_id == category_id:
                        stats.confirmed += 1
                    else:
                        stats.applied += 1
            await remember_ai_decision(
                session, workspace_id, stored_key(group.key), category_id,
                answer.confidence, answer.clean_name,
            )
            if allowed_category_ids is not None:
                await repoint_store(
                    session, workspace_id, stored_key(group.key), category_id,
                    from_ids=allowed_category_ids,
                )
            continue

        if allowed_category_ids is not None:
            # Undecided in a redistribution: the rows stay in the first child.
            stats.skipped += len(open_rows)
            continue
        for tx in open_rows:
            suggestions.append(_suggestion(workspace_id, run, group.key, tx, answer, now))
            decided.append(tx.id)
        if answer is None:
            stats.skipped += len(open_rows)
        else:
            stats.suggested += len(open_rows)

    await supersede_no_ai(session, workspace_id, decided)
    await _insert_suggestions(session, suggestions)
    await write_staged(session, staged)
    return stats


# --- calling Claude ---------------------------------------------------------------


def estimate_cost(params: dict[str, Any], *, batch: bool) -> Decimal:
    """Upper-ish bound of one request: prompt characters / 4 in, `max_tokens` out."""
    chars = sum(len(str(block.get("text", ""))) for block in params.get("system", []))
    chars += sum(len(str(m.get("content", ""))) for m in params.get("messages", []))
    usage = SimpleNamespace(
        input_tokens=math.ceil(chars / CHARS_PER_TOKEN), output_tokens=params["max_tokens"]
    )
    return cost_usd(params["model"], usage, batch=batch)


async def committed_spend(session: AsyncSession, workspace_id: uuid.UUID) -> Decimal:
    """The month's spend plus the estimate of the batches still in flight.

    A batch is paid when it ends, but the money is committed when it is
    sent: without this, a run started while another waits on its batch
    could spend past the cap. One statement, so both terms come from the
    same snapshot: a collection committing in between cannot move a batch
    from "estimate" to `cost_usd` unseen (the submitter holds only the spend
    lock, not the workspace lock `collect_batch` takes).
    """
    tz = await get_workspace_timezone(session, workspace_id)
    start, end = _month_bounds(datetime.now(tz))
    in_month = and_(CategorizationRun.created_at >= start, CategorizationRun.created_at < end)
    in_flight = and_(
        CategorizationRun.kind.in_(BATCH_KINDS),
        CategorizationRun.status == "processing_batch",
    )
    rows = (
        await session.execute(
            select(
                CategorizationRun.cost_usd,
                CategorizationRun.detail,
                case((in_month, True), else_=False).label("in_month"),
                case((in_flight, True), else_=False).label("in_flight"),
            ).where(CategorizationRun.workspace_id == workspace_id, or_(in_month, in_flight))
        )
    ).all()
    spent = Decimal(0)
    for cost, detail, month, flight in rows:
        if month:
            spent += Decimal(str(cost or 0))
        if flight:
            spent += batch_estimate(detail)
    return spent


def batch_estimate(detail: dict[str, Any] | None) -> Decimal:
    """The estimate a run stored when it sent its batch; 0 when absent."""
    info = (detail or {}).get(BATCH_DETAIL)
    raw = info.get("estimated_cost_usd") if isinstance(info, dict) else None
    try:
        return Decimal(str(raw)) if raw is not None else Decimal(0)
    except ArithmeticError:
        return Decimal(0)


def account_usage(run: CategorizationRun, usage: Any, *, batch: bool) -> None:
    """Add one paid call's tokens and cost to `run`."""
    def tokens(name: str) -> int:
        return int(getattr(usage, name, None) or 0)

    run.input_tokens = (run.input_tokens or 0) + tokens("input_tokens")
    run.output_tokens = (run.output_tokens or 0) + tokens("output_tokens")
    run.cache_write_tokens = (run.cache_write_tokens or 0) + tokens("cache_creation_input_tokens")
    run.cache_read_tokens = (run.cache_read_tokens or 0) + tokens("cache_read_input_tokens")
    run.cost_usd = Decimal(run.cost_usd or 0) + cost_usd(CLASSIFIER_MODEL, usage, batch=batch)


def _count(stats: dict[str, Any], result: ApplyStats) -> None:
    stats["applied"] = stats.get("applied", 0) + result.applied
    stats["confirmed"] = stats.get("confirmed", 0) + result.confirmed
    stats["suggested"] = stats.get("suggested", 0) + result.suggested


async def _apply_message(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run: CategorizationRun,
    groups_by_item: dict[str, MerchantGroup],
    message: Any,
    *,
    index: CategoryIndex,
    threshold: int,
    position: int,
    allowed_category_ids: set[uuid.UUID] | None = None,
) -> ApplyStats:
    items = _items(message)
    if items is None:
        # Run and chunk only: the request and the answer are never logged.
        logger.warning(
            "categorization run %s chunk %s: answer not usable (stop_reason=%s)",
            run.id, position, getattr(message, "stop_reason", None),
        )
    return await apply_results(
        session, workspace_id, run, groups_by_item, items or [],
        index=index, threshold=threshold, model=CLASSIFIER_MODEL,
        allowed_category_ids=allowed_category_ids,
    )


async def category_index(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    allowed_category_ids: set[uuid.UUID] | None = None,
) -> CategoryIndex:
    categories = (
        await session.execute(select(Category).where(Category.workspace_id == workspace_id))
    ).scalars().all()
    groups = (
        await session.execute(
            select(CategoryGroup).where(CategoryGroup.workspace_id == workspace_id)
        )
    ).scalars().all()
    return build_category_index(categories, groups, allowed_category_ids=allowed_category_ids)


async def _history(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    groups: dict[str, MerchantGroup],
    memory_rows: list[MerchantMemory],
    index: CategoryIndex,
) -> dict[str, list[tuple[str, int]]]:
    """The person's previous picks, for the stores where they are mixed."""
    hits = await lookup(session, workspace_id, list(groups))
    mixed = {key for key, hit in hits.items() if not hit.consistent and hit.distribution}
    return build_history([row for row in memory_rows if row.merchant_key in mixed], index)


def _plan(
    chunk: list[MerchantGroup],
    system: list[dict[str, Any]],
    schema: dict[str, Any],
    history: dict[str, list[tuple[str, int]]],
    notes: dict[str, str],
) -> _Plan:
    text, mapping = build_user_message(chunk, history, notes)
    by_key = {group.key: group for group in chunk}
    return _Plan(
        groups_by_item={item_id: by_key[key] for item_id, key in mapping.items()},
        params=classify_params(system, text, len(chunk), schema),
    )


def _notes(
    groups: dict[str, MerchantGroup], memory_rows: list[MerchantMemory]
) -> dict[str, str]:
    """Group key -> what the person wrote about that store, matched in the
    stored key form the memory uses."""
    by_stored = {row.merchant_key: row.note for row in memory_rows if row.note}
    return {key: note for key in groups if (note := by_stored.get(stored_key(key)))}


def _groups_of(plans: Sequence[_Plan]) -> dict[str, MerchantGroup]:
    return {g.key: g for plan in plans for g in plan.groups_by_item.values()}


def _unsent(
    groups: Sequence[MerchantGroup], allowed_category_ids: set[uuid.UUID] | None
) -> dict[str, Any]:
    """The `run.detail` patch of a capped run: a redistribution's rows left
    out (see `UNSENT_DETAIL`); nothing for a classification or no row."""
    tx_ids = [str(tx_id) for group in groups for tx_id in group.tx_ids]
    if allowed_category_ids is None or not tx_ids:
        return {}
    return {UNSENT_DETAIL: tx_ids}


async def _merge_detail(
    session: AsyncSession,
    run: CategorizationRun,
    patch: dict[str, Any],
    *,
    drop: tuple[str, ...] = (),
) -> None:
    """Change `run.detail` without losing another worker's marks.

    A trigger arriving while the run is busy sets `detail["rerun"]` with a
    read-modify-write under the workspace lock; taking the same lock before
    re-reading the column makes the two writes serialize either way.
    """
    from app.tasks import categorization_tasks as tasks

    await tasks.lock_workspace(session, run.workspace_id)
    stored = await session.scalar(
        select(CategorizationRun.detail).where(CategorizationRun.id == run.id)
    )
    detail = {**(stored or {}), **patch}
    for key in drop:
        detail.pop(key, None)
    run.detail = detail
    await session.flush()


async def classify(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    groups: dict[str, MerchantGroup],
    run: CategorizationRun,
    *,
    client: AsyncAnthropic,
    allowed_category_ids: set[uuid.UUID] | None = None,
) -> None:
    """Decide `groups` with Claude, within the workspace's monthly cap.

    Owns the run's counters from here (`run.stats` already holds the memory
    phase's). Leaves the run `running` (the pipeline closes it), `capped`,
    or `processing_batch`. API errors propagate: the task records them.
    `allowed_category_ids` restricts the choice to those categories and
    applies the answer as a redistribution (see `apply_results`).
    """
    stats = dict(run.stats or {})
    if not groups:
        close_stats(stats)
        run.stats = stats
        return
    settings = await get_settings(session, workspace_id)
    index = await category_index(session, workspace_id, allowed_category_ids)
    memory_rows = list(
        (
            await session.execute(
                select(MerchantMemory).where(MerchantMemory.workspace_id == workspace_id)
            )
        ).scalars().all()
    )
    history = await _history(session, workspace_id, groups, memory_rows, index)
    notes = _notes(groups, memory_rows)
    system = build_system_blocks(index, build_examples(memory_rows, index))
    schema = build_result_schema(index)
    ordered = [groups[key] for key in sorted(groups)]
    plans = [
        _plan(ordered[start:start + CHUNK_SIZE], system, schema, history, notes)
        for start in range(0, len(ordered), CHUNK_SIZE)
    ]
    cap = Decimal(settings.monthly_cap_usd)
    threshold = settings.auto_apply_threshold

    if len(ordered) > SYNC_LIMIT:
        await _submit_batch(
            session, workspace_id, run, plans, stats,
            client=client, index=index, cap=cap, allowed_category_ids=allowed_category_ids,
        )
    else:
        for position, plan in enumerate(plans):
            # Checked before every call against what the month already spent
            # (this run included), so a run stops as soon as the cap is near.
            spent = await committed_spend(session, workspace_id)
            if spent + estimate_cost(plan.params, batch=False) > cap:
                left = _groups_of(plans[position:])
                if unsent := _unsent(list(left.values()), allowed_category_ids):
                    await _merge_detail(session, run, unsent)
                run.status = "capped"
                stats["suggested"] = stats.get("suggested", 0) + await suggest_without_ai(
                    session, workspace_id, left, run.id
                )
                break
            run.model = CLASSIFIER_MODEL
            message = await create_message(client, plan.params)
            account_usage(run, getattr(message, "usage", None), batch=False)
            # The call is paid (see the module docstring), so its cost
            # must reach `month_spend` even if applying the answer fails and
            # the task rolls back. Nothing else is pending at this point
            # (the memory phase was committed; no row is locked yet), which
            # holds because `SYNC_LIMIT <= CHUNK_SIZE` makes this one call.
            await session.commit()
            _count(stats, await _apply_message(
                session, workspace_id, run, plan.groups_by_item, message,
                index=index, threshold=threshold, position=position,
                allowed_category_ids=allowed_category_ids,
            ))
    close_stats(stats)
    run.stats = stats


async def _submit_batch(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run: CategorizationRun,
    plans: list[_Plan],
    stats: dict[str, Any],
    *,
    client: AsyncAnthropic,
    index: CategoryIndex,
    cap: Decimal,
    allowed_category_ids: set[uuid.UUID] | None = None,
) -> None:
    """Send the chunks that fit under the cap as one Message Batch; the rest
    become model-less suggestions and the run ends `capped`.

    The workspace's spend lock is taken first and held until the batch's
    tracking is committed: two batches of one workspace are sent one at a
    time, so each sees the other's estimate in `committed_spend`. It is not
    the workspace lock, so a trigger or a POST /run does not wait for
    `create_batch` (a network call). The workspace lock is taken after it,
    before this run's row is written: a trigger holds that lock while it
    marks this row (`_mark_busy_run`), so taking it after an UPDATE of the
    row would deadlock against it.

    Once `create_batch` returns, the batch is paid: its tracking (`batch_id`,
    `processing_batch`, the estimate) is committed right away, before the
    suggestions for what was left out, and written again with a direct
    UPDATE when that commit fails (the error is then raised).
    """
    from app.tasks import categorization_tasks as tasks

    await tasks.lock_spend(session, workspace_id)
    spent = await committed_spend(session, workspace_id)
    total = spent
    sent: list[_Plan] = []
    for plan in plans:
        estimate = estimate_cost(plan.params, batch=True)
        if total + estimate > cap:
            break
        total += estimate
        sent.append(plan)
    left = plans[len(sent):]
    unsent = _unsent(list(_groups_of(left).values()), allowed_category_ids)
    if not sent:
        await tasks.lock_workspace(session, workspace_id)
        run.status = "capped"
        if unsent:
            await _merge_detail(session, run, unsent)
        stats["suggested"] = stats.get("suggested", 0) + await suggest_without_ai(
            session, workspace_id, _groups_of(plans), run.id
        )
        return

    requests = [
        Request(custom_id=batch_custom_id(run, position), params=cast(Any, plan.params))
        for position, plan in enumerate(sent)
    ]
    batch_id = await create_batch(client, requests)
    tracking = {
        "model": CLASSIFIER_MODEL,
        "is_batch": True,
        "batch_id": batch_id,
        "status": "processing_batch",
    }
    patch = {
        BATCH_DETAIL: {
            "chunks": [
                {item_id: group.key for item_id, group in plan.groups_by_item.items()}
                for plan in sent
            ],
            "rows": {
                group.key: [str(tx_id) for tx_id in group.tx_ids]
                for plan in sent
                for group in plan.groups_by_item.values()
            },
            "index": [[short, str(category_id)] for short, category_id, _ in index.entries],
            # A redistribution's family, so the collection applies it the same way.
            "allowed": (
                sorted(str(i) for i in allowed_category_ids)
                if allowed_category_ids is not None
                else None
            ),
            "capped": bool(left),
            # Counted by `committed_spend` while the batch is in flight.
            "estimated_cost_usd": str(total - spent),
        },
        **unsent,
    }
    # The batch is paid from here on: its tracking is committed before any
    # other work, so nothing failing later can roll it back (the run then
    # stays `processing_batch` and the beat collects it, see
    # `tasks.record_failure`). The commit also frees the spend lock.
    run_id = run.id
    try:
        await tasks.lock_workspace(session, workspace_id)
        for name, value in tracking.items():
            setattr(run, name, value)
        await _merge_detail(session, run, patch)
        await session.commit()
    except Exception:
        await session.rollback()
        await _track_batch(session, run_id, workspace_id, tracking, patch)
        raise
    # Held until the task commits, as before the commit above: this run's
    # row is written again (stats, status) after this point.
    await tasks.lock_workspace(session, workspace_id)
    if left:
        stats["suggested"] = stats.get("suggested", 0) + await suggest_without_ai(
            session, workspace_id, _groups_of(left), run.id
        )


async def _track_batch(
    session: AsyncSession,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    tracking: dict[str, Any],
    patch: dict[str, Any],
) -> None:
    """Write a sent batch's tracking with a direct UPDATE, in a transaction
    of its own, when the regular write failed: the batch is paid and must
    stay collectable. Same locks and order as `_submit_batch` (spend, then
    workspace), and the stored `detail` is merged, not replaced."""
    from app.tasks import categorization_tasks as tasks

    await tasks.lock_spend(session, workspace_id)
    await tasks.lock_workspace(session, workspace_id)
    stored = await session.scalar(
        select(CategorizationRun.detail).where(CategorizationRun.id == run_id)
    )
    await session.execute(
        update(CategorizationRun)
        .where(CategorizationRun.id == run_id)
        .values(**tracking, detail={**(stored or {}), **patch})
        .execution_options(synchronize_session=False)
    )
    await session.commit()


# --- collecting a batch -----------------------------------------------------------


def _stored_groups(chunk: dict[str, str], rows: dict[str, list[str]]) -> dict[str, MerchantGroup]:
    """The chunk's stores rebuilt from `run.detail` (only key and rows matter)."""
    groups: dict[str, MerchantGroup] = {}
    for item_id, key in chunk.items():
        tx_ids = [uuid.UUID(raw) for raw in rows.get(key, [])]
        groups[item_id] = MerchantGroup(
            key=key, tx_ids=tx_ids, samples=[], count=len(tx_ids),
            median_amount=Decimal(0), type="debit", recurring=False, account_type=None,
        )
    return groups


def batch_custom_id(run: CategorizationRun, position: int) -> str:
    return f"{run.id.hex}{CUSTOM_ID_SEPARATOR}{position}"


def _chunk_position(custom_id: str, run: CategorizationRun, size: int) -> int | None:
    prefix, _, raw = custom_id.rpartition(CUSTOM_ID_SEPARATOR)
    if prefix != run.id.hex or not raw.isdigit() or int(raw) >= size:
        return None
    return int(raw)


async def collect_batch(session: AsyncSession, run_id: uuid.UUID) -> str:
    """Apply a finished batch of a `processing_batch` run.

    Returns "pending" (not ended yet), "collected", "failed" (no usable key
    or nothing to apply it with: the run is closed as failed, its rows are
    picked up by the next run) or "gone" (the run is no longer waiting).
    A request that errored, expired or was canceled leaves its stores as
    undecided suggestions. The batch is read from the API first; the
    workspace lock is taken only to apply it, and held until the caller
    commits.
    """
    from app.tasks import categorization_tasks as tasks

    run = await session.get(CategorizationRun, run_id, populate_existing=True)
    if run is None or run.status != "processing_batch" or not run.batch_id:
        return "gone"
    workspace_id, batch_id = run.workspace_id, run.batch_id
    settings = await get_settings(session, workspace_id)
    has_detail = isinstance((run.detail or {}).get(BATCH_DETAIL), dict)
    client = await resolve_client(session, settings) if has_detail else None
    results: list[tuple[str, Any, str | None]] = []
    if client is not None:
        try:
            if await batch_status(client, batch_id) != "ended":
                return "pending"
            results = [entry async for entry in batch_results(client, batch_id)]
        finally:
            await client.close()
    # Two overlapping polls must not apply the same batch twice: the second
    # waits here, then finds the run closed. Taken before any row lock, in
    # the order `_claim` takes it, so the two cannot deadlock.
    await tasks.lock_workspace(session, workspace_id)
    run = await session.get(CategorizationRun, run_id, populate_existing=True)
    if run is None or run.status != "processing_batch" or run.batch_id != batch_id:
        return "gone"
    info = (run.detail or {}).get(BATCH_DETAIL)
    if client is None or not isinstance(info, dict):
        run.status = "failed"
        run.error = "no_ai_connection" if isinstance(info, dict) else "batch_detail_missing"
        run.finished_at = datetime.now(timezone.utc)
        await session.flush()
        return "failed"
    return await _collect(session, run, info, results, settings.auto_apply_threshold)


async def _collect(
    session: AsyncSession,
    run: CategorizationRun,
    info: dict[str, Any],
    results: list[tuple[str, Any, str | None]],
    threshold: int,
) -> str:
    index = CategoryIndex(
        tuple((short, uuid.UUID(raw), "") for short, raw in info.get("index", []))
    )
    chunks: list[dict[str, str]] = info.get("chunks", [])
    rows: dict[str, list[str]] = info.get("rows", {})
    raw_allowed = info.get("allowed")
    allowed = (
        {uuid.UUID(raw) for raw in raw_allowed} if isinstance(raw_allowed, list) else None
    )
    stats = dict(run.stats or {})
    done: set[int] = set()
    unanswered: list[MerchantGroup] = []
    for custom_id, message, error in results:
        position = _chunk_position(custom_id, run, len(chunks))
        if position is None or position in done:
            continue
        done.add(position)
        groups = _stored_groups(chunks[position], rows)
        if message is None:
            unanswered.extend(groups.values())
            logger.warning(
                "categorization run %s: batch request %s ended %s", run.id, position, error
            )
            result = await apply_results(
                session, run.workspace_id, run, groups, [],
                index=index, threshold=threshold, model=CLASSIFIER_MODEL,
                allowed_category_ids=allowed,
            )
        else:
            account_usage(run, getattr(message, "usage", None), batch=True)
            result = await _apply_message(
                session, run.workspace_id, run, groups, message,
                index=index, threshold=threshold, position=position,
                allowed_category_ids=allowed,
            )
        _count(stats, result)
    for position, chunk in enumerate(chunks):
        if position not in done:
            groups = _stored_groups(chunk, rows)
            unanswered.extend(groups.values())
            _count(stats, await apply_results(
                session, run.workspace_id, run, groups, [],
                index=index, threshold=threshold, model=CLASSIFIER_MODEL,
                allowed_category_ids=allowed,
            ))
    close_stats(stats)
    run.stats = stats
    run.status = "capped" if info.get("capped") else "done"
    run.finished_at = datetime.now(timezone.utc)
    patch: dict[str, Any] = {}
    # Only a redistribution has rows to name: they stay in the first child,
    # where nothing else asks about them, so the run must stay retryable.
    if more := _unsent(unanswered, allowed).get(UNSENT_DETAIL):
        if run.status == "done":
            run.status, run.error = "failed", INCOMPLETE_ERROR
        stored = (run.detail or {}).get(UNSENT_DETAIL)
        patch[UNSENT_DETAIL] = [*(stored if isinstance(stored, list) else []), *more]
    await _merge_detail(session, run, patch, drop=(BATCH_DETAIL,))
    return "collected"


# --- the pipeline's seam ----------------------------------------------------------


async def classify_with_claude(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    groups: dict[str, MerchantGroup],
    run: CategorizationRun,
) -> None:
    """`pipeline.ai_classifier`: Claude when the workspace has a usable key,
    model-less suggestions otherwise."""
    client = None
    if groups:
        client = await resolve_client(session, await get_settings(session, workspace_id))
    if client is None:
        await suggest_for_run(session, workspace_id, groups, run)
        return
    try:
        await classify(session, workspace_id, groups, run, client=client)
    finally:
        await client.close()


pipeline.ai_classifier = classify_with_claude
