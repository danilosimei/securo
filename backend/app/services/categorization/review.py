"""What the person sees and does: the review queue, the audit list, undo.

The pipeline leaves two things for a person: pending suggestions (stores it
could not settle) and the changes it applied on its own. This module reads
both for the UI and carries out the person's answers -- accept, dismiss,
create the proposed category, apply to similar rows -- always as source
`user`, through the set-based recorder. A decision about a store teaches
the memory once, not once per row: the bulk is `derived` and the memory is
fed explicitly.

Nothing here commits: the API owns the transaction.
"""
from __future__ import annotations

import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSuggestion,
)
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.schemas.category import CategoryCreate
from app.schemas.category_group import CategoryGroupCreate
from app.services import category_group_service, category_service
from app.services.categorization import events, memory
from app.services.categorization.costs import month_spend
from app.services.categorization.events import (
    PROTECTED_SOURCES,
    record_changes_bulk,
)
from app.services.categorization.merchant_key import (
    PAYEE_PREFIX,
    PERSON_PREFIX,
    UNKNOWN_KEY,
    merchant_key,
    sample_description,
    scrub_documents,
)
from app.services.categorization.pipeline import CHUNK, MAX_SAMPLES, stored_key
from app.services.categorization.settings import find_ai_connection, get_settings

#: Origins of the changes the pipeline made on its own (the audit list).
AUTO_SOURCES: tuple[str, ...] = ("ai", "memory", "rule")
#: Pipeline decisions an undo sends back to review instead of to the pipeline.
REVIEWED_ON_UNDO: tuple[str, ...] = ("ai", "memory")
#: `reason` of the suggestion an undone pipeline decision leaves behind.
UNDONE_REASON = "desfeito por você"
#: A new category is proposed only when this many stores ask for it.
MIN_PROPOSAL_STORES = 3
#: Events shown in a transaction's "how it got here".
PROVENANCE_EVENTS = 5
APPLIED_WINDOW = timedelta(days=7)
#: `error` of a manual run the broker refused.
ENQUEUE_FAILED = "enqueue_failed"
#: Prefix of a person key built from a document hash: never a rule pattern.
HASHED_PERSON_PREFIX = PERSON_PREFIX + "#"


class NotFound(Exception):
    """A row the request names is not in the workspace."""


class NoSafePattern(ValueError):
    """The merchant key cannot become a rule without leaking or overmatching."""


def _chunks(items: list[Any], size: int = CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fold(text: str) -> str:
    """Uppercase, accents removed: how the rule engine compares text."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _norm_name(text: str) -> str:
    return " ".join(_fold(text).split())


def _open_row(workspace_id: uuid.UUID):
    """Join a suggestion to its row, if nobody categorized that row by hand
    since: a suggestion over a protected row is stale, not a question."""
    return and_(
        Transaction.id == CategorizationSuggestion.transaction_id,
        Transaction.workspace_id == workspace_id,
        Transaction.category_source.not_in(PROTECTED_SOURCES),
    )


# --- summary ----------------------------------------------------------------


async def summary(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Any]:
    groups, txs = (
        await session.execute(
            select(
                func.count(func.distinct(CategorizationSuggestion.merchant_key)),
                func.count(func.distinct(CategorizationSuggestion.transaction_id)),
            )
            .join(Transaction, _open_row(workspace_id))
            .where(
                CategorizationSuggestion.workspace_id == workspace_id,
                CategorizationSuggestion.status == "pending",
            )
        )
    ).one()
    applied = await session.scalar(
        select(func.count(CategorizationEvent.id)).where(
            CategorizationEvent.workspace_id == workspace_id,
            CategorizationEvent.source.in_(AUTO_SOURCES),
            CategorizationEvent.undone_at.is_(None),
            CategorizationEvent.created_at >= _now() - APPLIED_WINDOW,
        )
    )
    last = (
        await session.execute(
            select(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "classify",
            )
            .order_by(CategorizationRun.created_at.desc(), CategorizationRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    settings = await get_settings(session, workspace_id)
    return {
        "pending_groups": groups or 0,
        "pending_transactions": txs or 0,
        "applied_last_7d": applied or 0,
        "last_run": (
            {
                "id": last.id,
                "status": last.status,
                "finished_at": last.finished_at,
                "stats": dict(last.stats or {}),
            }
            if last is not None
            else None
        ),
        "ai_available": await find_ai_connection(session, settings) is not None,
        "month_cost_usd": await month_spend(session, workspace_id),
        "monthly_cap_usd": settings.monthly_cap_usd,
    }


# --- review queue -------------------------------------------------------------


@dataclass
class _Group:
    key: str
    clean_name: str | None = None
    suggestion: dict[str, Any] | None = None
    new_category: dict[str, str] | None = None
    total: Decimal = Decimal("0")
    tx_ids: list[uuid.UUID] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)


def _new_category(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    group, name = value.get("group"), value.get("name")
    if not isinstance(group, str) or not isinstance(name, str):
        return None
    if not group.strip() or not name.strip():
        return None
    return {"group": group.strip(), "name": name.strip()}


async def _pending_groups(session: AsyncSession, workspace_id: uuid.UUID) -> list[_Group]:
    """Pending suggestions by store, biggest total first (ties by key)."""
    rows = await session.execute(
        select(
            CategorizationSuggestion,
            Transaction.description,
            Transaction.amount,
            Transaction.amount_primary,
        )
        .join(Transaction, _open_row(workspace_id))
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
        )
        # Newest first, so a group's suggestion is its latest opinion.
        .order_by(CategorizationSuggestion.created_at.desc(), CategorizationSuggestion.id)
    )
    groups: dict[str, _Group] = {}
    seen: set[uuid.UUID] = set()
    for sug, description, amount, amount_primary in rows.all():
        if sug.transaction_id in seen:
            continue
        seen.add(sug.transaction_id)
        group = groups.setdefault(sug.merchant_key, _Group(key=sug.merchant_key))
        group.tx_ids.append(sug.transaction_id)
        group.descriptions.append(description or "")
        group.total += abs(amount_primary if amount_primary is not None else amount or 0)
        if group.clean_name is None and sug.clean_name:
            group.clean_name = sug.clean_name
        if group.suggestion is None and (
            sug.category_id is not None or sug.alt_category_id is not None
        ):
            group.suggestion = {
                "category_id": sug.category_id,
                "alt_category_id": sug.alt_category_id,
                "confidence": sug.confidence,
                "reason": sug.reason,
            }
        if group.new_category is None:
            group.new_category = _new_category(sug.new_category)
    return sorted(groups.values(), key=lambda g: (-g.total, g.key))


def _samples(descriptions: list[str]) -> list[str]:
    counts = Counter(sample_description(d) for d in descriptions)
    return [text for text, _ in counts.most_common() if text][:MAX_SAMPLES]


def _proposals(groups: list[_Group]) -> list[dict[str, Any]]:
    """New categories at least `MIN_PROPOSAL_STORES` stores ask for."""
    by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        if group.new_category is None:
            continue
        norm = (_norm_name(group.new_category["group"]), _norm_name(group.new_category["name"]))
        entry = by_name.setdefault(
            norm,
            {
                "group": group.new_category["group"],
                "name": group.new_category["name"],
                "merchant_keys": [],
                "count": 0,
                "total": Decimal("0"),
            },
        )
        entry["merchant_keys"].append(group.key)
        entry["count"] += len(group.tx_ids)
        entry["total"] += group.total
    found = [p for p in by_name.values() if len(p["merchant_keys"]) >= MIN_PROPOSAL_STORES]
    return sorted(found, key=lambda p: (-p["total"], p["name"]))


async def review_queue(
    session: AsyncSession, workspace_id: uuid.UUID, *, limit: int, offset: int
) -> dict[str, Any]:
    groups = await _pending_groups(session, workspace_id)
    page = groups[offset:offset + limit]
    return {
        "groups": [
            {
                "merchant_key": g.key,
                "clean_name": g.clean_name,
                "count": len(g.tx_ids),
                "total": g.total,
                "samples": _samples(g.descriptions),
                "suggestion": g.suggestion,
                "new_category": g.new_category,
                "is_person": g.key.startswith(PERSON_PREFIX),
                "transaction_ids": g.tx_ids,
            }
            for g in page
        ],
        "total": len(groups),
        "new_category_proposals": _proposals(groups),
    }


# --- the person's answers -----------------------------------------------------


@dataclass
class Applied:
    applied: int
    event_ids: list[uuid.UUID]


async def _load_transactions(
    session: AsyncSession, workspace_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[Transaction]:
    found: list[Transaction] = []
    for chunk in _chunks(ids):
        result = await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id, Transaction.id.in_(chunk)
            )
        )
        found.extend(result.scalars().all())
    return found


async def _apply_as_user(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    txs: list[Transaction],
    category_id: uuid.UUID,
) -> Applied:
    """Set `category_id` on `txs` as the person's choice; one event insert.

    `derived`: the caller decides what the memory learns (once per store).
    Returns the events written, so the UI can offer "undo".
    """
    started = _now()
    applied = await record_changes_bulk(
        session,
        workspace_id,
        [(tx, category_id, "user") for tx in txs],
        user_id=user_id,
        derived=True,
    )
    event_ids: list[uuid.UUID] = []
    for chunk in _chunks([tx.id for tx in txs]):
        event_ids.extend(
            (
                await session.execute(
                    select(CategorizationEvent.id).where(
                        CategorizationEvent.workspace_id == workspace_id,
                        CategorizationEvent.transaction_id.in_(chunk),
                        CategorizationEvent.source == "user",
                        CategorizationEvent.user_id == user_id,
                        CategorizationEvent.undone_at.is_(None),
                        CategorizationEvent.created_at >= started,
                    )
                )
            ).scalars().all()
        )
    return Applied(applied=applied, event_ids=event_ids)


async def _pending_suggestions(
    session: AsyncSession, workspace_id: uuid.UUID, keys: list[str]
) -> list[CategorizationSuggestion]:
    if not keys:
        return []
    result = await session.execute(
        select(CategorizationSuggestion).where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
            CategorizationSuggestion.merchant_key.in_(keys),
        )
    )
    return list(result.scalars().all())


async def _resolve(
    session: AsyncSession, suggestions: list[CategorizationSuggestion], status: str
) -> None:
    ids = [s.id for s in suggestions]
    now = _now()
    for chunk in _chunks(ids):
        await session.execute(
            sa_update(CategorizationSuggestion)
            .where(CategorizationSuggestion.id.in_(chunk))
            .values(status=status, resolved_at=now)
            .execution_options(synchronize_session="fetch")
        )


async def _assignable_category(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> None:
    if category_id not in await category_service.get_assignable_category_ids(
        session, workspace_id
    ):
        raise NotFound("Category not found")


async def _accept_keys(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    keys: list[str],
    category_id: uuid.UUID,
    transaction_ids: list[uuid.UUID] | None = None,
) -> Applied:
    """Accept stores' pending rows (or the chosen ones) under `category_id`.

    A row someone categorized by hand since the suggestion was made keeps
    that choice, even when selected; its suggestion is settled all the same.
    """
    suggestions = await _pending_suggestions(session, workspace_id, keys)
    if transaction_ids is not None:
        wanted = set(transaction_ids)
        suggestions = [s for s in suggestions if s.transaction_id in wanted]
    tx_ids = list(dict.fromkeys(s.transaction_id for s in suggestions))
    txs = [
        tx
        for tx in await _load_transactions(session, workspace_id, tx_ids)
        if tx.category_source not in PROTECTED_SOURCES
    ]
    result = await _apply_as_user(session, workspace_id, user_id, txs, category_id)
    for key in dict.fromkeys(s.merchant_key for s in suggestions):
        await memory.learn_choice(session, workspace_id, key, category_id)
    await _resolve(session, suggestions, "accepted")
    return result


async def accept(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    merchant_key: str,
    category_id: uuid.UUID,
    transaction_ids: list[uuid.UUID] | None = None,
) -> Applied:
    """Apply a category to a store's pending rows (or the chosen ones)."""
    await _assignable_category(session, workspace_id, category_id)
    return await _accept_keys(
        session, workspace_id, user_id, [merchant_key], category_id, transaction_ids
    )


async def dismiss(session: AsyncSession, workspace_id: uuid.UUID, merchant_key: str) -> int:
    """Drop a store's pending suggestions; its rows keep their category."""
    suggestions = await _pending_suggestions(session, workspace_id, [merchant_key])
    await _resolve(session, suggestions, "dismissed")
    return len(suggestions)


async def _find_or_create_group(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, name: str
) -> CategoryGroup:
    wanted = _norm_name(name)
    groups = (
        await session.execute(
            select(CategoryGroup).where(CategoryGroup.workspace_id == workspace_id)
        )
    ).scalars().all()
    for group in groups:
        # A hidden group would hide the category created in it.
        if not group.is_hidden and _norm_name(group.name) == wanted:
            return group
    return await category_group_service.create_group(
        session, workspace_id, user_id, CategoryGroupCreate(name=name.strip())
    )


async def create_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    group: str,
    name: str,
    merchant_keys: list[str],
) -> tuple[uuid.UUID, Applied]:
    """Create a proposed category (and its group) and file the stores under it.

    An existing visible group, or assignable category, of the same
    (normalized) name is reused; a hidden one is not.
    The category services commit on their own; the rest is the caller's.
    """
    category_group = await _find_or_create_group(session, workspace_id, user_id, group)
    wanted = _norm_name(name)
    existing = (
        await session.execute(
            select(Category).where(
                Category.workspace_id == workspace_id,
                Category.group_id == category_group.id,
            )
        )
    ).scalars().all()
    assignable = await category_service.get_assignable_category_ids(session, workspace_id)
    category = next(
        (c for c in existing if c.id in assignable and _norm_name(c.name) == wanted), None
    )
    if category is None:
        category = await category_service.create_category(
            session,
            workspace_id,
            user_id,
            CategoryCreate(name=name.strip(), group_id=category_group.id),
        )
    keys = list(dict.fromkeys(k for k in merchant_keys if k))
    result = await _accept_keys(session, workspace_id, user_id, keys, category.id)
    # The tree changed: cached model decisions were made without this category.
    await memory.forget_ai_decisions(session, workspace_id)
    return category.id, result


# --- audit list, undo, provenance ---------------------------------------------


async def applied_events(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    sources: tuple[str, ...] = AUTO_SOURCES,
    min_confidence: int | None = None,
    max_confidence: int | None = None,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Changes the pipeline made on its own and nobody undid, newest first."""
    filters = [
        CategorizationEvent.workspace_id == workspace_id,
        CategorizationEvent.source.in_(sources),
        CategorizationEvent.undone_at.is_(None),
    ]
    if min_confidence is not None:
        filters.append(CategorizationEvent.confidence >= min_confidence)
    if max_confidence is not None:
        filters.append(CategorizationEvent.confidence <= max_confidence)
    if since is not None:
        filters.append(CategorizationEvent.created_at >= since)
    joined = and_(
        Transaction.id == CategorizationEvent.transaction_id,
        Transaction.workspace_id == workspace_id,
    )
    total = await session.scalar(
        select(func.count(CategorizationEvent.id)).join(Transaction, joined).where(*filters)
    )
    rows = await session.execute(
        select(CategorizationEvent, Transaction)
        .join(Transaction, joined)
        .where(*filters)
        .order_by(CategorizationEvent.created_at.desc(), CategorizationEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        {
            "event_id": ev.id,
            "run_id": ev.run_id,
            "transaction": {
                "id": tx.id,
                "date": tx.date,
                "description": tx.description,
                "amount": tx.amount,
                "currency": tx.currency,
            },
            "source": ev.source,
            "previous_category_id": ev.previous_category_id,
            "new_category_id": ev.new_category_id,
            "confidence": ev.confidence,
            "reason": ev.reason,
            "created_at": ev.created_at,
        }
        for ev, tx in rows.all()
    ]
    return items, total or 0


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def undo(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    event_ids: list[uuid.UUID] | None = None,
    run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None,
) -> events.UndoResult:
    """`events.undo`, plus what the undone decisions had settled.

    Undoing a person's change (accept, create-category, apply-to-similar)
    takes back what it taught the memory, once per store and category, and
    reopens the suggestion it accepted, so the store is back in review.

    Undoing a pipeline decision (`ai`, `memory`) drops the model's cached
    decision for the store and leaves the row a pending suggestion with the
    undone category as the alternative, so it goes to Revisar instead of
    being re-applied (or paid for again) by the next run.
    """
    restorable: list[CategorizationEvent] = []
    automatic: list[CategorizationEvent] = []
    if event_ids is not None or run_id is not None:
        query = select(CategorizationEvent).where(
            CategorizationEvent.workspace_id == workspace_id,
            CategorizationEvent.source.in_(("user", *REVIEWED_ON_UNDO)),
            CategorizationEvent.undone_at.is_(None),
        )
        if event_ids is not None:
            query = query.where(CategorizationEvent.id.in_(event_ids))
        if run_id is not None:
            query = query.where(CategorizationEvent.run_id == run_id)
        for ev in (await session.execute(query)).scalars().all():
            (restorable if ev.source == "user" else automatic).append(ev)

    result = await events.undo(
        session, workspace_id, event_ids=event_ids, run_id=run_id, user_id=user_id
    )
    # Same session, same identity map: `events.undo` stamped these objects.
    restored = [ev for ev in restorable if ev.undone_at is not None]
    if restored:
        await _take_back(session, workspace_id, restored)
    undone = [ev for ev in automatic if ev.undone_at is not None]
    if undone:
        await _back_to_review(session, workspace_id, undone)
    return result


async def _back_to_review(
    session: AsyncSession, workspace_id: uuid.UUID, undone: list[CategorizationEvent]
) -> None:
    # The latest undone decision per row is the one the person saw.
    latest: dict[uuid.UUID, CategorizationEvent] = {}
    for ev in sorted(undone, key=lambda e: _utc(e.created_at), reverse=True):
        latest.setdefault(ev.transaction_id, ev)
    tx_ids = list(latest)
    txs = {tx.id: tx for tx in await _load_transactions(session, workspace_id, tx_ids)}
    pending: set[uuid.UUID] = set()
    for chunk in _chunks(tx_ids):
        pending.update(
            (
                await session.execute(
                    select(CategorizationSuggestion.transaction_id).where(
                        CategorizationSuggestion.workspace_id == workspace_id,
                        CategorizationSuggestion.transaction_id.in_(chunk),
                        CategorizationSuggestion.status == "pending",
                    )
                )
            ).scalars().all()
        )
    keys: dict[str, None] = {}
    payload: list[dict[str, Any]] = []
    now = _now()
    for tx_id, ev in latest.items():
        tx = txs.get(tx_id)
        if tx is None or tx.category_source in PROTECTED_SOURCES:
            continue
        key = stored_key(await memory.key_for_transaction(session, tx))
        keys[key] = None
        if tx_id in pending:
            continue
        payload.append({
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "transaction_id": tx_id,
            "merchant_key": key,
            "alt_category_id": ev.new_category_id,
            "reason": UNDONE_REASON,
            "status": "pending",
            "created_at": now,
        })
    if keys:
        await memory.forget_ai_decisions(session, workspace_id, list(keys))
    if payload:
        await session.execute(sa_insert(CategorizationSuggestion), payload)
    await session.flush()


async def _take_back(
    session: AsyncSession, workspace_id: uuid.UUID, restored: list[CategorizationEvent]
) -> None:
    tx_ids = list(dict.fromkeys(ev.transaction_id for ev in restored))
    accepted: dict[uuid.UUID, CategorizationSuggestion] = {}
    still_pending: set[uuid.UUID] = set()
    for chunk in _chunks(tx_ids):
        rows = await session.execute(
            select(CategorizationSuggestion)
            .where(
                CategorizationSuggestion.workspace_id == workspace_id,
                CategorizationSuggestion.transaction_id.in_(chunk),
                CategorizationSuggestion.status.in_(("accepted", "pending")),
            )
            .order_by(CategorizationSuggestion.resolved_at.desc())
        )
        for sug in rows.scalars().all():
            if sug.status == "pending":
                still_pending.add(sug.transaction_id)
            else:
                accepted.setdefault(sug.transaction_id, sug)
    txs = {tx.id: tx for tx in await _load_transactions(session, workspace_id, tx_ids)}

    taught: dict[tuple[str, uuid.UUID], None] = {}
    for ev in restored:
        tx = txs.get(ev.transaction_id)
        sug = accepted.get(ev.transaction_id)
        # Only the suggestion this decision settled (accepted at or after it).
        if sug is not None and (
            sug.resolved_at is None or _utc(sug.resolved_at) < _utc(ev.created_at)
        ):
            sug = None
        if sug is not None:
            key = sug.merchant_key
            if (
                tx is not None
                and tx.category_source not in PROTECTED_SOURCES
                and tx.id not in still_pending
            ):
                sug.status = "pending"
                sug.resolved_at = None
                still_pending.add(tx.id)
        elif tx is not None:
            key = stored_key(await memory.key_for_transaction(session, tx))
        else:
            continue
        if ev.new_category_id is not None:
            taught[(key, ev.new_category_id)] = None
    for key, category_id in taught:
        await memory.unlearn_choice(session, workspace_id, key, category_id)
    await session.flush()


async def get_transaction(
    session: AsyncSession, workspace_id: uuid.UUID, tx_id: uuid.UUID
) -> Transaction:
    tx = await session.scalar(
        select(Transaction).where(Transaction.id == tx_id, Transaction.workspace_id == workspace_id)
    )
    if tx is None:
        raise NotFound("Transaction not found")
    return tx


async def provenance(
    session: AsyncSession, workspace_id: uuid.UUID, tx_id: uuid.UUID
) -> dict[str, Any]:
    tx = await get_transaction(session, workspace_id, tx_id)
    events = (
        await session.execute(
            select(CategorizationEvent)
            .where(
                CategorizationEvent.workspace_id == workspace_id,
                CategorizationEvent.transaction_id == tx.id,
            )
            .order_by(CategorizationEvent.created_at.desc(), CategorizationEvent.id.desc())
            .limit(PROVENANCE_EVENTS)
        )
    ).scalars().all()
    return {
        "current_source": tx.category_source,
        "original_description": tx.original_description,
        "events": list(events),
    }


# --- similar rows, rule draft ---------------------------------------------------


async def similar(
    session: AsyncSession, workspace_id: uuid.UUID, tx_id: uuid.UUID
) -> tuple[Transaction, str, list[uuid.UUID]]:
    """Rows of the same store, not protected, filed elsewhere than the reference.

    A row with no merchant identity (`UNKNOWN`) has no "same store": none.
    """
    ref = await get_transaction(session, workspace_id, tx_id)
    key = await memory.key_for_transaction(session, ref)
    if key == UNKNOWN_KEY:
        return ref, key, []
    rows = await session.execute(
        select(
            Transaction.id,
            Transaction.description,
            Payee.name.label("payee_name"),
            Payee.source.label("payee_source"),
        )
        .outerjoin(
            Payee, and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.id != ref.id,
            Transaction.category_source.not_in(PROTECTED_SOURCES),
            Transaction.category_id.is_distinct_from(ref.category_id),
            Transaction.transfer_pair_id.is_(None),
            Transaction.source != "opening_balance",
        )
        .order_by(Transaction.date, Transaction.id)
    )
    ids = [
        row.id
        for row in rows.all()
        if merchant_key(row.description, payee_name=row.payee_name, payee_source=row.payee_source)
        == key
    ]
    return ref, key, ids


async def apply_similar(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, tx_id: uuid.UUID
) -> Applied:
    """File the similar rows under the reference's category, as the person.

    One decision about the store: the memory learns it once (the reference's
    category may have come from the model, a rule or the bank, never from
    the person's hook), and the rows themselves are derived.
    """
    ref, key, ids = await similar(session, workspace_id, tx_id)
    if key == UNKNOWN_KEY:
        raise NoSafePattern("sem padrão seguro para esta transação")
    if ref.category_id is None:
        raise ValueError("A transação de referência não tem categoria")
    txs = [
        tx
        for tx in await _load_transactions(session, workspace_id, ids)
        if tx.category_source not in PROTECTED_SOURCES
    ]
    result = await _apply_as_user(session, workspace_id, user_id, txs, ref.category_id)
    if result.applied:
        await memory.learn_choice(session, workspace_id, stored_key(key), ref.category_id)
    return result


async def rule_draft(
    session: AsyncSession, workspace_id: uuid.UUID, tx_id: uuid.UUID
) -> dict[str, Any]:
    """A rule for this store, from its merchant key (no model involved).

    `description contains <key>` with the "P:" / "PIX:" prefix stripped. A
    payee or person key whose name is not in the description falls back to
    the payee itself; a person key made from a document hash never becomes a pattern
    (it would match nothing, or leak the document): the payee, or nothing.
    """
    tx = await get_transaction(session, workspace_id, tx_id)
    key = await memory.key_for_transaction(session, tx)
    payee_name = None
    by_payee = None
    if tx.payee_id is not None:
        payee_name = await session.scalar(
            select(Payee.name).where(Payee.id == tx.payee_id, Payee.workspace_id == workspace_id)
        )
        if payee_name is not None:
            by_payee = {"field": "payee_id", "op": "equals", "value": str(tx.payee_id)}
    description = _fold(tx.description or "")

    if key.startswith(HASHED_PERSON_PREFIX) or key == UNKNOWN_KEY:
        if by_payee is None:
            raise NoSafePattern("sem padrão seguro para esta transação")
        name = scrub_documents(payee_name or "").strip() or "Pix"
        condition = by_payee
    elif key.startswith(PAYEE_PREFIX):
        value = key[len(PAYEE_PREFIX):].strip()
        name = value
        if (value and value in description) or by_payee is None:
            condition = {"field": "description", "op": "contains", "value": value}
        else:
            condition = by_payee
    elif key.startswith(PERSON_PREFIX):
        # The name may come from the payee, not the description: then only
        # the payee identifies the person.
        value = key[len(PERSON_PREFIX):].strip()
        name = value
        if value and value in description:
            condition = {"field": "description", "op": "contains", "value": value}
        elif by_payee is not None:
            condition = by_payee
        else:
            raise NoSafePattern("sem padrão seguro para esta transação")
    else:
        value = key.strip()
        name = value
        condition = {"field": "description", "op": "contains", "value": value}

    if condition["field"] == "description" and not condition["value"]:
        raise NoSafePattern("sem padrão seguro para esta transação")
    actions = (
        [{"op": "set_category", "value": str(tx.category_id)}]
        if tx.category_id is not None
        else []
    )
    return {"name": name[:100], "conditions": [condition], "actions": actions}


# --- manual run -------------------------------------------------------------------


async def request_run(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[CategorizationRun, bool]:
    """The busy `classify` run, or a new queued manual one; (run, created).

    Takes the workspace lock the worker takes, so two clicks do not queue
    two runs; abandoned runs are expired first so they cannot block forever.
    """
    from app.tasks import categorization_tasks as tasks

    await tasks.lock_workspace(session, workspace_id)
    await tasks.expire_stale_runs(session, workspace_id)
    busy = (
        await session.execute(
            select(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "classify",
                CategorizationRun.status.in_(tasks.BUSY_STATUSES),
            )
            .order_by(CategorizationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if busy is not None:
        return busy, False
    run = CategorizationRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind="classify",
        trigger="manual",
        status="queued",
        stats={},
        detail={},
        created_at=_now(),
    )
    session.add(run)
    await session.flush()
    return run, True


async def mark_enqueue_failed(session: AsyncSession, run: CategorizationRun) -> None:
    run.status = "failed"
    run.error = ENQUEUE_FAILED
    run.finished_at = _now()
    await session.flush()
