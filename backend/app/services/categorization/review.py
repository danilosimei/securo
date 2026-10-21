"""What the person sees and does: the review queue, the audit list, undo.

The pipeline leaves two things for a person: pending suggestions (stores it
could not settle) and the changes it applied on its own. This module reads
both for the UI and carries out the person's answers -- accept, dismiss,
create the proposed category, apply to similar rows -- always as source
`user`, through the set-based recorder. A decision about a store teaches
the memory once, not once per row: the bulk is `derived` and the memory is
fed explicitly, and one of its events is marked `memory_pick`, so an undo
(whole or row by row) takes back exactly that one pick.

Nothing here commits: the API owns the transaction.
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSuggestion,
)
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services import category_service
from app.services.categorization import creation, events, memory
from app.services.categorization.costs import month_spend
from app.services.categorization.events import (
    PROTECTED_SOURCES,
    changes_category,
    record_changes_bulk,
)
from app.services.categorization.merchant_key import (
    PAYEE_PREFIX,
    PERSON_PREFIX,
    UNKNOWN_KEY,
    is_unnamed_key,
    merchant_key,
    sample_description,
    scrub_documents,
)
from app.services.categorization.pipeline import CHUNK, MAX_SAMPLES, categorizable, stored_key
from app.services.categorization.prompts import prompt_sample
from app.services.categorization.settings import find_ai_connection, get_settings

#: Origins of the changes the pipeline made on its own (the audit list).
AUTO_SOURCES: tuple[str, ...] = ("ai", "memory", "rule")
#: Origins of the decisions that confirm a category (Aplicadas > Confirmadas).
CONFIRMING_SOURCES: tuple[str, ...] = ("ai", "memory")
#: Row origins of Aplicadas > Suas: the person's, then or before the feature.
USER_ORIGINS: tuple[str, ...] = ("user", "legacy")
#: Row origins of Aplicadas > Outras origens: the bank, imports, series.
OTHER_ORIGINS: tuple[str, ...] = ("provider", "import", "recurring", "installment")
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
#: Rows of a store the review queue carries, so opening it costs no request.
REVIEW_PREVIEW = 10


class NotFound(Exception):
    """A row the request names is not in the workspace."""


class NoSafePattern(ValueError):
    """The merchant key cannot become a rule without leaking or overmatching."""


def _chunks(items: list[Any], size: int = CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: How names and descriptions are compared (shared with `creation`).
_fold = creation.fold
_norm_name = creation.norm_name


def open_transaction(workspace_id: uuid.UUID) -> list[Any]:
    """A row a pending suggestion may still ask about: categorizable (the
    pipeline's base) and not categorized by hand since."""
    return [
        *categorizable(workspace_id),
        Transaction.category_source.not_in(PROTECTED_SOURCES),
    ]


def open_row(workspace_id: uuid.UUID):
    """Join a suggestion to its row, if the row is still open: a suggestion
    over a row someone categorized by hand since, or that became one side
    of a transfer, an ignored row or an opening balance, is stale, not a
    question (Revisar, its badge and the scoreboard agree on this)."""
    return and_(
        Transaction.id == CategorizationSuggestion.transaction_id,
        *open_transaction(workspace_id),
    )


def standing_event() -> Any:
    """Events still in force: not undone, and no later standing event on
    the same row (a person, or a later run, decided it since). The rule
    `events.undo` applies: a superseded event cannot be undone either."""
    later = aliased(CategorizationEvent)
    return and_(
        CategorizationEvent.undone_at.is_(None),
        ~(
            select(later.id)
            .where(
                later.transaction_id == CategorizationEvent.transaction_id,
                later.undone_at.is_(None),
                later.source != "undo",
                later.created_at > CategorizationEvent.created_at,
            )
            .exists()
        ),
    )


# --- summary ----------------------------------------------------------------


def _latest_pending(workspace_id: uuid.UUID) -> Any:
    """Pending suggestions over open rows, each with its rank among its
    row's (1 = the latest, as `_pending_groups` picks it)."""
    return (
        select(
            CategorizationSuggestion.transaction_id.label("transaction_id"),
            CategorizationSuggestion.merchant_key.label("merchant_key"),
            func.row_number()
            .over(
                partition_by=CategorizationSuggestion.transaction_id,
                order_by=(CategorizationSuggestion.created_at.desc(), CategorizationSuggestion.id),
            )
            .label("rank"),
        )
        .join(Transaction, open_row(workspace_id))
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
        )
        .subquery()
    )


async def summary(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Any]:
    # The badge counts what Revisar lists: each row once, under the store of
    # its latest pending suggestion.
    latest = _latest_pending(workspace_id)
    groups, txs = (
        await session.execute(
            select(
                func.count(func.distinct(latest.c.merchant_key)),
                func.count(latest.c.transaction_id),
            ).where(latest.c.rank == 1)
        )
    ).one()
    applied = await session.scalar(
        select(func.count(CategorizationEvent.id)).where(
            CategorizationEvent.workspace_id == workspace_id,
            CategorizationEvent.source.in_(AUTO_SOURCES),
            # What the default Aplicadas list shows (`applied_events`).
            standing_event(),
            CategorizationEvent.created_at >= _now() - APPLIED_WINDOW,
            changes_category(),
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
        "ai_enabled": settings.enabled,
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
    #: The first `REVIEW_PREVIEW` rows: the description as it is now, plus
    #: the bank's original when it was rewritten since.
    preview: list[dict[str, Any]] = field(default_factory=list)


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
            Transaction.date,
            Transaction.type,
            Transaction.currency,
            Transaction.original_description,
        )
        .join(Transaction, open_row(workspace_id))
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
        )
        # Newest first, so a group's suggestion is its latest opinion.
        .order_by(CategorizationSuggestion.created_at.desc(), CategorizationSuggestion.id)
    )
    groups: dict[str, _Group] = {}
    seen: set[uuid.UUID] = set()
    for sug, description, amount, amount_primary, day, kind, currency, original in rows.all():
        if sug.transaction_id in seen:
            continue
        seen.add(sug.transaction_id)
        group = groups.setdefault(sug.merchant_key, _Group(key=sug.merchant_key))
        group.tx_ids.append(sug.transaction_id)
        group.descriptions.append(description or "")
        if len(group.preview) < REVIEW_PREVIEW:
            group.preview.append({
                "id": sug.transaction_id,
                "date": day,
                "description": description,
                "original_description": original,
                "amount": amount,
                "currency": currency,
                "type": kind,
            })
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
    hits = await memory.lookup(session, workspace_id, [g.key for g in page])
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
                "note": hits[g.key].note,
                "transaction_ids": g.tx_ids,
                "transactions": g.preview,
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
    #: Transaction id -> the event written for it.
    by_tx: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    #: The rows that changed, in the order they were given.
    transaction_ids: list[uuid.UUID] = field(default_factory=list)


async def load_transactions(
    session: AsyncSession, workspace_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[Transaction]:
    """The workspace's rows among `ids` (others are left out), per chunk,
    in the order of `ids`."""
    found: dict[uuid.UUID, Transaction] = {}
    for chunk in _chunks(ids):
        result = await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id, Transaction.id.in_(chunk)
            )
        )
        found.update((tx.id, tx) for tx in result.scalars().all())
    return [found[tx_id] for tx_id in dict.fromkeys(ids) if tx_id in found]


async def apply_as_user(
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
    by_tx: dict[uuid.UUID, uuid.UUID] = {}
    for chunk in _chunks([tx.id for tx in txs]):
        by_tx.update(
            (tx_id, event_id)
            for event_id, tx_id in (
                await session.execute(
                    select(CategorizationEvent.id, CategorizationEvent.transaction_id).where(
                        CategorizationEvent.workspace_id == workspace_id,
                        CategorizationEvent.transaction_id.in_(chunk),
                        CategorizationEvent.source == "user",
                        CategorizationEvent.user_id == user_id,
                        CategorizationEvent.undone_at.is_(None),
                        CategorizationEvent.created_at >= started,
                    )
                )
            ).all()
        )
    changed = list(dict.fromkeys(tx.id for tx in txs if tx.id in by_tx))
    return Applied(
        applied=applied,
        event_ids=[by_tx[tx_id] for tx_id in changed],
        by_tx=by_tx,
        transaction_ids=changed,
    )


async def _pending_suggestions(
    session: AsyncSession, workspace_id: uuid.UUID, keys: list[str]
) -> list[CategorizationSuggestion]:
    """The stores' pending suggestions, store by store in the order of
    `keys`, each store's newest first (the order Revisar lists its rows)."""
    if not keys:
        return []
    result = await session.execute(
        select(CategorizationSuggestion)
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
            CategorizationSuggestion.merchant_key.in_(keys),
        )
        .order_by(CategorizationSuggestion.created_at.desc(), CategorizationSuggestion.id)
    )
    rank = {key: i for i, key in enumerate(dict.fromkeys(keys))}
    return sorted(result.scalars().all(), key=lambda s: rank[s.merchant_key])


async def _load_open(
    session: AsyncSession, workspace_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[Transaction]:
    """The rows among `ids` still open (`open_transaction`), in that order."""
    found: dict[uuid.UUID, Transaction] = {}
    for chunk in _chunks(ids):
        result = await session.execute(
            select(Transaction).where(Transaction.id.in_(chunk), *open_transaction(workspace_id))
        )
        found.update((tx.id, tx) for tx in result.scalars().all())
    return [found[tx_id] for tx_id in dict.fromkeys(ids) if tx_id in found]


async def resolve_suggestions(
    session: AsyncSession, suggestions: list[CategorizationSuggestion], status: str
) -> None:
    """Settle `suggestions` as `status` (accepted, dismissed), stamped now."""
    ids = [s.id for s in suggestions]
    now = _now()
    for chunk in _chunks(ids):
        await session.execute(
            sa_update(CategorizationSuggestion)
            .where(CategorizationSuggestion.id.in_(chunk))
            .values(status=status, resolved_at=now)
            .execution_options(synchronize_session="fetch")
        )


async def ensure_assignable(
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
    """Accept stores' pending rows (or the chosen ones) under `category_id`,
    in the order of `transaction_ids`, else in Revisar's (`_pending_suggestions`).

    A row no longer open -- someone categorized it by hand since the
    suggestion was made, or it left the categorizable base -- keeps what it
    has, even when selected; its suggestion is settled all the same.
    """
    suggestions = await _pending_suggestions(session, workspace_id, keys)
    if transaction_ids is not None:
        wanted = set(transaction_ids)
        suggestions = [s for s in suggestions if s.transaction_id in wanted]
        suggested = {s.transaction_id for s in suggestions}
        tx_ids = [tx_id for tx_id in dict.fromkeys(transaction_ids) if tx_id in suggested]
    else:
        tx_ids = list(dict.fromkeys(s.transaction_id for s in suggestions))
    txs = await _load_open(session, workspace_id, tx_ids)
    result = await apply_as_user(session, workspace_id, user_id, txs, category_id)
    # One pick per store, carried by one of the store's events. A store none
    # of whose rows was filed (all categorized by hand since) teaches
    # nothing: no event would carry the pick, so no undo could take it back.
    carriers: dict[str, uuid.UUID] = {}
    for sug in suggestions:
        event_id = result.by_tx.get(sug.transaction_id)
        if event_id is not None:
            carriers.setdefault(sug.merchant_key, event_id)
    for key in carriers:
        await memory.learn_choice(session, workspace_id, key, category_id)
    await events.mark_picks(session, list(carriers.values()))
    await resolve_suggestions(session, suggestions, "accepted")
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
    await ensure_assignable(session, workspace_id, category_id)
    return await _accept_keys(
        session, workspace_id, user_id, [merchant_key], category_id, transaction_ids
    )


async def dismiss(session: AsyncSession, workspace_id: uuid.UUID, merchant_key: str) -> int:
    """Drop a store's pending suggestions; its rows keep their category."""
    suggestions = await _pending_suggestions(session, workspace_id, [merchant_key])
    await resolve_suggestions(session, suggestions, "dismissed")
    return len(suggestions)


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
    (normalized) name is reused; a hidden one is not (`creation`). Nothing
    commits here (the category services only flush): a failure while filing
    the stores takes the new category and group back with everything else.
    """
    created = await creation.find_or_create_category(
        session, workspace_id, user_id, group=group, name=name
    )
    keys = list(dict.fromkeys(k for k in merchant_keys if k))
    result = await _accept_keys(session, workspace_id, user_id, keys, created.category_id)
    return created.category_id, result


# --- audit list, undo, provenance ---------------------------------------------


def readable_key(key: str) -> str:
    """A store key as a person would write it: no prefix, title case."""
    for prefix in (PAYEE_PREFIX, PERSON_PREFIX):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    return key.strip().title()


def key_name(key: str) -> str | None:
    """`readable_key`, or None for a key that must not name anybody
    (`merchant_key.is_unnamed_key`)."""
    if is_unnamed_key(key):
        return None
    return readable_key(key) or None


async def _store_labels(
    session: AsyncSession, workspace_id: uuid.UUID, txs: list[Transaction]
) -> dict[uuid.UUID, tuple[str, str]]:
    """Transaction id -> (store key, display name), in two queries for the page.

    The key only groups (never shown). The name is, in order: the latest
    suggestion's clean name, the payee's name when the key came from it, the
    readable key, and for a hashed Pix key or an unknown store the scrubbed
    description (no documents, no account numbers).
    """
    if not txs:
        return {}
    payee_ids = {tx.payee_id for tx in txs if tx.payee_id is not None}
    payees: dict[uuid.UUID, tuple[str, str | None]] = {}
    if payee_ids:
        payee_rows = await session.execute(
            select(Payee.id, Payee.name, Payee.source).where(Payee.id.in_(payee_ids))
        )
        payees = {pid: (name, source) for pid, name, source in payee_rows.all()}
    clean: dict[uuid.UUID, str] = {}
    sug_rows = await session.execute(
        select(CategorizationSuggestion.transaction_id, CategorizationSuggestion.clean_name)
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.transaction_id.in_([tx.id for tx in txs]),
            CategorizationSuggestion.clean_name.is_not(None),
        )
        .order_by(CategorizationSuggestion.created_at.desc(), CategorizationSuggestion.id)
    )
    for tx_id, name in sug_rows.all():
        if name and name.strip():
            clean.setdefault(tx_id, name.strip())
    out: dict[uuid.UUID, tuple[str, str]] = {}
    for tx in txs:
        payee_name, payee_source = payees.get(tx.payee_id, (None, None)) if tx.payee_id else (None, None)
        key = merchant_key(tx.description, payee_name=payee_name, payee_source=payee_source)
        if tx.id in clean:
            name = clean[tx.id]
        elif key.startswith(PAYEE_PREFIX) and payee_name and payee_name.strip():
            name = payee_name.strip()
        elif is_unnamed_key(key):
            name = prompt_sample(tx.description) or readable_key(key)
        else:
            name = readable_key(key)
        out[tx.id] = (stored_key(key), name)
    return out


def _applied_item(
    tx: Transaction,
    ev: CategorizationEvent | None,
    label: tuple[str, str],
    *,
    source: str,
    new_category_id: uuid.UUID | None,
    created_at: datetime,
) -> dict[str, Any]:
    """One line of Aplicadas: the row, the event behind it (if any), the store."""
    return {
        "event_id": ev.id if ev is not None else None,
        "run_id": ev.run_id if ev is not None else None,
        "transaction": {
            "id": tx.id,
            "date": tx.date,
            "description": tx.description,
            "amount": tx.amount,
            "currency": tx.currency,
        },
        "source": source,
        "previous_category_id": ev.previous_category_id if ev is not None else None,
        "new_category_id": new_category_id,
        "confidence": ev.confidence if ev is not None else None,
        "reason": ev.reason if ev is not None else None,
        "created_at": created_at,
        "merchant_key": label[0],
        "store_name": label[1],
    }


async def applied_events(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    sources: tuple[str, ...] = AUTO_SOURCES,
    min_confidence: int | None = None,
    max_confidence: int | None = None,
    since: datetime | None = None,
    confirmed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Changes the pipeline made on its own still in force, newest first:
    one undone, or superseded by a later decision on the same row (it shows
    a category the row may no longer have, and its undo would be skipped),
    is left out.

    Confirmations (the category did not change) are no change to check, so
    they are left out -- unless `confirmed`, which lists only them: the AI
    or the memory settling a row at the category it already held (from the
    bank, say). A "confirmation" of no category is none, and stays out.
    """
    filters = [
        CategorizationEvent.workspace_id == workspace_id,
        standing_event(),
    ]
    if confirmed:
        filters += [
            CategorizationEvent.source.in_(CONFIRMING_SOURCES),
            ~changes_category(),
            CategorizationEvent.new_category_id.is_not(None),
        ]
    else:
        filters += [CategorizationEvent.source.in_(sources), changes_category()]
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
    pairs = rows.all()
    labels = await _store_labels(session, workspace_id, [tx for _, tx in pairs])
    items = [
        _applied_item(
            tx, ev, labels[tx.id],
            source=ev.source, new_category_id=ev.new_category_id, created_at=ev.created_at,
        )
        for ev, tx in pairs
    ]
    return items, total or 0


async def _standing_events(
    session: AsyncSession, workspace_id: uuid.UUID, tx_ids: list[uuid.UUID]
) -> dict[uuid.UUID, CategorizationEvent]:
    """Each row's latest standing event (not undone, not an undo)."""
    latest: dict[uuid.UUID, CategorizationEvent] = {}
    for chunk in _chunks(tx_ids):
        result = await session.execute(
            select(CategorizationEvent)
            .where(
                CategorizationEvent.workspace_id == workspace_id,
                CategorizationEvent.transaction_id.in_(chunk),
                CategorizationEvent.undone_at.is_(None),
                CategorizationEvent.source != "undo",
            )
            .order_by(CategorizationEvent.created_at.desc(), CategorizationEvent.id.desc())
        )
        for ev in result.scalars().all():
            latest.setdefault(ev.transaction_id, ev)
    return latest


async def origin_rows(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    origins: tuple[str, ...],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Categorizable rows with a category from `origins`, newest first.

    By origin, not by event, so a choice made before events existed (or by
    the bank, an import, a series) is listed too. Each line carries the
    row's latest standing event when it has one (else no `event_id`: no
    undo), its origin as `source` and its current category.
    """
    filters = [
        *categorizable(workspace_id),
        Transaction.category_id.is_not(None),
        Transaction.category_source.in_(origins),
    ]
    total = await session.scalar(select(func.count(Transaction.id)).where(*filters))
    txs = list(
        (
            await session.execute(
                select(Transaction)
                .where(*filters)
                .order_by(Transaction.created_at.desc(), Transaction.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    )
    latest = await _standing_events(session, workspace_id, [tx.id for tx in txs])
    labels = await _store_labels(session, workspace_id, txs)
    items = [
        _applied_item(
            tx, latest.get(tx.id), labels[tx.id],
            source=tx.category_source, new_category_id=tx.category_id, created_at=tx.created_at,
        )
        for tx in txs
    ]
    return items, total or 0


async def scoreboard(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, int]:
    """How far categorization got over the categorizable rows.

    `to_review`: rows with a pending suggestion Revisar shows (over an open
    row: one over a row the person categorized since is stale). `decided`:
    the other rows with a category, whatever it is -- one that keeps money
    out of spending (`treat_as_transfer`, e.g. buying a flat) is a decision
    all the same. `waiting`: no category and nothing to review yet. The
    three always add up to `total`.
    """
    pending = (
        select(CategorizationSuggestion.id)
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
            open_row(workspace_id),
        )
        .exists()
    )
    to_review = case((pending, 1), else_=0)
    decided = case((and_(~pending, Transaction.category_id.is_not(None)), 1), else_=0)
    total, review_count, decided_count = (
        await session.execute(
            select(
                func.count(Transaction.id),
                func.coalesce(func.sum(to_review), 0),
                func.coalesce(func.sum(decided), 0),
            ).where(*categorizable(workspace_id))
        )
    ).one()
    total, review_count, decided_count = int(total or 0), int(review_count), int(decided_count)
    return {
        "total": total,
        "decided": decided_count,
        "to_review": review_count,
        "waiting": total - decided_count - review_count,
    }


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

    Undoing a person's change (accept, create-category, apply-to-similar,
    an edit) takes back the pick it taught the memory and reopens the
    suggestion it accepted, so the store is back in review. Undoing a
    person's clear gives the store back the pick the clear took (R27-U); the
    AI's cached decision the clear dropped stays dropped. Either way only an
    event marked `memory_pick` moves a pick, exactly one: a change taught
    once per store carries the mark on one of its events, so undoing it whole
    or row by row reverses that pick once, and a clear that found no pick to
    take (or only followed another row's) gives none back.

    Undoing a pipeline decision (`ai`, `memory`) drops the model's cached
    decision for the store and leaves the row a pending suggestion with the
    undone category as the alternative, so it goes to Revisar instead of
    being re-applied (or paid for again) by the next run.

    Undoing a run ("Desfazer este lote") takes back only what it changed: a
    guess it confirmed at the same category stays settled, out of review.
    An undo by event ids reverts whatever it names.
    """
    restorable: list[CategorizationEvent] = []
    automatic: list[CategorizationEvent] = []
    cleared: list[CategorizationEvent] = []
    if event_ids is not None or run_id is not None:
        query = select(CategorizationEvent).where(
            CategorizationEvent.workspace_id == workspace_id,
            or_(
                CategorizationEvent.source.in_(("user", *REVIEWED_ON_UNDO)),
                # A person's clear (`events.cleared_by_person` reads the same).
                and_(
                    CategorizationEvent.source == "none",
                    CategorizationEvent.user_id.is_not(None),
                    CategorizationEvent.new_category_id.is_(None),
                    CategorizationEvent.previous_category_id.is_not(None),
                ),
            ),
            CategorizationEvent.undone_at.is_(None),
        )
        if event_ids is not None:
            query = query.where(CategorizationEvent.id.in_(event_ids))
        if run_id is not None:
            query = query.where(CategorizationEvent.run_id == run_id, changes_category())
        for ev in (await session.execute(query)).scalars().all():
            if ev.source == "user":
                restorable.append(ev)
            elif ev.source == "none":
                cleared.append(ev)
            else:
                automatic.append(ev)

    result = await events.undo(
        session, workspace_id, event_ids=event_ids, run_id=run_id, user_id=user_id,
        changes_only=run_id is not None,
    )
    # Same session, same identity map: `events.undo` stamped these objects.
    restored = [ev for ev in restorable if ev.undone_at is not None]
    if restored:
        await _take_back(session, workspace_id, restored)
    undone = [ev for ev in automatic if ev.undone_at is not None]
    if undone:
        await _back_to_review(session, workspace_id, undone)
    given_back = [ev for ev in cleared if ev.undone_at is not None]
    if given_back:
        await _give_back(session, workspace_id, given_back)
    return result


async def _give_back(
    session: AsyncSession, workspace_id: uuid.UUID, uncleared: list[CategorizationEvent]
) -> None:
    """The pick each undone clear had taken back (`memory_pick`), one each:
    a clear that took none, or only followed another row's, gives none."""
    took = [ev for ev in uncleared if ev.memory_pick and ev.previous_category_id is not None]
    if not took:
        return
    txs = await load_transactions(
        session, workspace_id, list(dict.fromkeys(ev.transaction_id for ev in took))
    )
    store_keys = await memory.keys_for(session, txs)
    for ev in took:
        key = store_keys.get(ev.transaction_id)
        if key is not None and ev.previous_category_id is not None:
            await memory.learn_choice(session, workspace_id, key, ev.previous_category_id)


async def _back_to_review(
    session: AsyncSession, workspace_id: uuid.UUID, undone: list[CategorizationEvent]
) -> None:
    # The latest undone decision per row is the one the person saw.
    latest: dict[uuid.UUID, CategorizationEvent] = {}
    for ev in sorted(undone, key=lambda e: _utc(e.created_at), reverse=True):
        latest.setdefault(ev.transaction_id, ev)
    tx_ids = list(latest)
    txs = {tx.id: tx for tx in await load_transactions(session, workspace_id, tx_ids)}
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
    store_keys = await memory.keys_for(session, list(txs.values()))
    keys: dict[str, None] = {}
    payload: list[dict[str, Any]] = []
    now = _now()
    for tx_id, ev in latest.items():
        tx = txs.get(tx_id)
        if tx is None or tx.category_source in PROTECTED_SOURCES:
            continue
        key = store_keys[tx_id]
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
    txs = {tx.id: tx for tx in await load_transactions(session, workspace_id, tx_ids)}
    store_keys = await memory.keys_for(session, list(txs.values()))

    taught: list[tuple[str, uuid.UUID]] = []
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
            key = store_keys[tx.id]
        else:
            continue
        # Only the event that carried the pick takes it back, once.
        if ev.memory_pick and ev.new_category_id is not None:
            taught.append((key, ev.new_category_id))
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
    key = memory.stored_key(await memory.key_for_transaction(session, tx))
    note = (await memory.lookup(session, workspace_id, [key]))[key].note
    return {
        "current_source": tx.category_source,
        "original_description": tx.original_description,
        "events": list(events),
        "store_note": {"merchant_key": key, "note": note} if note else None,
    }


# --- similar rows, rule draft ---------------------------------------------------


@dataclass(frozen=True)
class SimilarRow:
    id: uuid.UUID
    date: date_type
    description: str
    amount: Decimal
    currency: str
    type: str
    category_id: uuid.UUID | None
    category_source: str


async def similar(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    tx_id: uuid.UUID,
    *,
    exclude_rule: bool = False,
) -> tuple[Transaction, str, list[SimilarRow]]:
    """Rows of the same store, not protected, filed elsewhere than the reference.

    `exclude_rule` also leaves out the rows a rule filed (the Categorization
    section); the other screens' correction toast keeps them. A row with no
    merchant identity (`UNKNOWN`) has no "same store": none.
    """
    ref = await get_transaction(session, workspace_id, tx_id)
    key = await memory.key_for_transaction(session, ref)
    if key == UNKNOWN_KEY:
        return ref, key, []
    excluded = PROTECTED_SOURCES | {"rule"} if exclude_rule else PROTECTED_SOURCES
    rows = await session.execute(
        select(
            Transaction.id,
            Transaction.date,
            Transaction.description,
            Transaction.amount,
            Transaction.currency,
            Transaction.type,
            Transaction.category_id,
            Transaction.category_source,
            Payee.name.label("payee_name"),
            Payee.source.label("payee_source"),
        )
        .outerjoin(
            Payee, and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.id != ref.id,
            Transaction.category_source.not_in(excluded),
            Transaction.category_id.is_distinct_from(ref.category_id),
            Transaction.transfer_pair_id.is_(None),
            Transaction.source != "opening_balance",
        )
        .order_by(Transaction.date, Transaction.id)
    )
    found = [
        SimilarRow(
            id=row.id,
            date=row.date,
            description=row.description,
            amount=row.amount,
            currency=row.currency,
            type=row.type,
            category_id=row.category_id,
            category_source=row.category_source,
        )
        for row in rows.all()
        if merchant_key(row.description, payee_name=row.payee_name, payee_source=row.payee_source)
        == key
    ]
    return ref, key, found


async def apply_similar(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    tx_id: uuid.UUID,
    *,
    exclude_rule: bool = False,
    transaction_ids: list[uuid.UUID] | None = None,
) -> Applied:
    """File the similar rows under the reference's category, as the person.

    One decision about the store: the memory learns it once (the reference's
    category may have come from the model, a rule or the bank, never from
    the person's hook), and the rows themselves are derived. With
    `transaction_ids`, only the intersection with the similar set is applied
    (the rows the person was shown).
    """
    ref, key, found = await similar(session, workspace_id, tx_id, exclude_rule=exclude_rule)
    if key == UNKNOWN_KEY:
        raise NoSafePattern("sem padrão seguro para esta transação")
    if ref.category_id is None:
        raise ValueError("A transação de referência não tem categoria")
    ids = [row.id for row in found]
    if transaction_ids is not None:
        wanted = set(transaction_ids)
        ids = [i for i in ids if i in wanted]
    txs = [
        tx
        for tx in await load_transactions(session, workspace_id, ids)
        if tx.category_source not in PROTECTED_SOURCES
    ]
    result = await apply_as_user(session, workspace_id, user_id, txs, ref.category_id)
    if result.applied:
        await memory.learn_choice(session, workspace_id, stored_key(key), ref.category_id)
        await events.mark_picks(session, result.event_ids[:1])
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

    if is_unnamed_key(key):
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
    two runs; abandoned runs are expired first so they cannot block forever
    (a run waiting on its batch is left to the beat, which collects it
    first). When another transaction holds the lock, a busy run still
    within `STALE_AFTER` is returned without waiting for it.
    """
    from app.tasks import categorization_tasks as tasks

    busy_query = (
        select(CategorizationRun)
        .where(
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.kind == "classify",
            CategorizationRun.status.in_(tasks.BUSY_STATUSES),
        )
        .order_by(CategorizationRun.created_at.desc())
        .limit(1)
    )
    if not await tasks.try_lock_workspace(session, workspace_id):
        now = _now()
        fresh = or_(*(
            and_(CategorizationRun.status == status, CategorizationRun.created_at >= now - age)
            for status, age in tasks.STALE_AFTER.items()
        ))
        busy = (await session.execute(busy_query.where(fresh))).scalar_one_or_none()
        if busy is not None:
            return busy, False
        # No live classification to show: wait for the holder. Every holder
        # is short (a claim, an organizer claim, a batch being applied):
        # sending or reading a batch happens outside this lock.
        await tasks.lock_workspace(session, workspace_id)
    await tasks.expire_stale_runs(session, workspace_id)
    busy = (await session.execute(busy_query)).scalar_one_or_none()
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
