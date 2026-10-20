"""Per-merchant memory: what this workspace taught us about each store.

Two things live in a `MerchantMemory` row. `counts` is what the person chose
(fed only by their own, non-derived categorizations) and the memory applies a
category only when it is both repeated and dominant. The `ai_*` columns cache
the model's decision for the store so the next transaction of the same store
costs nothing; a person's choice clears that cache. A person clearing a
store's category takes one pick for it back and clears the cache too (R27-U).

Nothing here commits: the caller owns the transaction.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import MerchantMemory
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services.categorization import events
from app.services.categorization.merchant_key import merchant_key

logger = logging.getLogger(__name__)

#: Choices needed before the memory is trusted.
MIN_CHOICES = 2
#: Share of the choices the dominant category must hold.
DOMINANCE = 0.8
#: `merchant_key` column width (memory and suggestions).
KEY_WIDTH = 255
#: Ids per `IN (...)` (asyncpg caps a statement at 32767 parameters).
CHUNK = 1000


@dataclass(frozen=True)
class MemoryHit:
    category_id: uuid.UUID | None
    consistent: bool
    distribution: dict[str, int] = field(default_factory=dict)
    ai_category_id: uuid.UUID | None = None
    ai_confidence: int | None = None
    ai_clean_name: str | None = None


def stored_key(key: str) -> str:
    return key[:KEY_WIDTH]


def _counts(raw: Any) -> dict[str, int]:
    """`counts` as stored, minus entries that are not a category id with a
    whole number of picks: one corrupt entry must not fail a whole run."""
    counts: dict[str, int] = {}
    for raw_id, raw_count in (raw if isinstance(raw, dict) else {}).items():
        try:
            uuid.UUID(raw_id)
            counts[raw_id] = int(raw_count)
        except (TypeError, ValueError, AttributeError):
            continue
    return counts


def is_consistent(counts: dict[str, int]) -> tuple[uuid.UUID | None, bool]:
    """(dominant category, whether it is repeated and dominant enough).

    Consistent means at least `MIN_CHOICES` choices in total and the top
    category holding at least `DOMINANCE` of them.
    """
    positive = {k: v for k, v in counts.items() if v > 0}
    if not positive:
        return None, False
    top_key = max(positive, key=lambda k: positive[k])
    total = sum(positive.values())
    top = positive[top_key]
    return uuid.UUID(top_key), total >= MIN_CHOICES and top >= DOMINANCE * total


async def key_for_transaction(session: AsyncSession, tx: Transaction) -> str:
    """The merchant key of a transaction, loading its payee explicitly."""
    payee_name = payee_source = None
    if tx.payee_id is not None:
        row = (
            await session.execute(
                select(Payee.name, Payee.source).where(Payee.id == tx.payee_id)
            )
        ).first()
        if row is not None:
            payee_name, payee_source = row
    return merchant_key(tx.description, payee_name=payee_name, payee_source=payee_source)


async def keys_for(session: AsyncSession, txs: list[Transaction]) -> dict[uuid.UUID, str]:
    """Transaction id -> stored store key, the payees loaded per chunk
    (`key_for_transaction` costs a query per row)."""
    payee_ids = list({tx.payee_id for tx in txs if tx.payee_id is not None})
    payees: dict[uuid.UUID, tuple[str, str | None]] = {}
    for start in range(0, len(payee_ids), CHUNK):
        rows = await session.execute(
            select(Payee.id, Payee.name, Payee.source).where(
                Payee.id.in_(payee_ids[start:start + CHUNK])
            )
        )
        payees.update((pid, (name, source)) for pid, name, source in rows.all())
    keys: dict[uuid.UUID, str] = {}
    for tx in txs:
        name, source = payees.get(tx.payee_id, (None, None)) if tx.payee_id else (None, None)
        keys[tx.id] = stored_key(merchant_key(tx.description, payee_name=name, payee_source=source))
    return keys


async def _get_or_create(
    session: AsyncSession, workspace_id: uuid.UUID, key: str
) -> MerchantMemory:
    query = select(MerchantMemory).where(
        MerchantMemory.workspace_id == workspace_id, MerchantMemory.merchant_key == key
    )
    row = (await session.execute(query)).scalar_one_or_none()
    if row is not None:
        return row
    row = MerchantMemory(id=uuid.uuid4(), workspace_id=workspace_id, merchant_key=key, counts={})
    try:
        # A savepoint, so losing a race on the unique key leaves the
        # caller's transaction usable.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        return row
    except IntegrityError:
        # The savepoint rollback already expunged `row`.
        return (await session.execute(query)).scalar_one()


async def learn_choice(
    session: AsyncSession, workspace_id: uuid.UUID, key: str, category_id: uuid.UUID
) -> None:
    """Count a person's pick for a store and drop the AI's cached decision.

    The cached clean name stays: it describes the store, not a category.
    """
    row = await _get_or_create(session, workspace_id, key)
    counts = _counts(row.counts)
    counts[str(category_id)] = counts.get(str(category_id), 0) + 1
    row.counts = counts  # reassigned: in-place JSON mutation is not tracked
    row.last_user_category_id = category_id
    row.ai_category_id = None
    row.ai_confidence = None
    await session.flush()


async def unlearn_choice(
    session: AsyncSession, workspace_id: uuid.UUID, key: str, category_id: uuid.UUID
) -> None:
    """Take back one pick for a store (an undone decision); never below zero.

    An entry that reaches zero is removed. The AI's cached decision, already
    dropped when the pick was learned, is not brought back.
    """
    row = (
        await session.execute(
            select(MerchantMemory).where(
                MerchantMemory.workspace_id == workspace_id, MerchantMemory.merchant_key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    counts = dict(row.counts or {})
    entry = str(category_id)
    try:
        current = int(counts.get(entry, 0) or 0)
    except (TypeError, ValueError):
        current = 0
    if current > 1:
        counts[entry] = current - 1
    else:
        counts.pop(entry, None)
    row.counts = counts  # reassigned: in-place JSON mutation is not tracked
    if row.last_user_category_id == category_id and entry not in counts:
        row.last_user_category_id = None
    await session.flush()


async def lookup(
    session: AsyncSession, workspace_id: uuid.UUID, keys: list[str]
) -> dict[str, MemoryHit]:
    """One `MemoryHit` per requested key (empty for stores never seen)."""
    unique = list(dict.fromkeys(keys))
    found: dict[str, MerchantMemory] = {}
    if unique:
        rows = (
            await session.execute(
                select(MerchantMemory).where(
                    MerchantMemory.workspace_id == workspace_id,
                    MerchantMemory.merchant_key.in_(unique),
                )
            )
        ).scalars().all()
        found = {r.merchant_key: r for r in rows}
    hits: dict[str, MemoryHit] = {}
    for key in unique:
        row = found.get(key)
        if row is None:
            hits[key] = MemoryHit(None, False)
            continue
        counts = _counts(row.counts)
        category_id, consistent = is_consistent(counts)
        hits[key] = MemoryHit(
            category_id=category_id,
            consistent=consistent,
            distribution=counts,
            ai_category_id=row.ai_category_id,
            ai_confidence=row.ai_confidence,
            ai_clean_name=row.ai_clean_name,
        )
    return hits


async def remember_ai_decision(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    key: str,
    category_id: uuid.UUID,
    confidence: int,
    clean_name: str | None,
) -> None:
    """Cache the model's decision for a store."""
    row = await _get_or_create(session, workspace_id, key)
    row.ai_category_id = category_id
    row.ai_confidence = confidence
    row.ai_clean_name = clean_name[:255] if clean_name else None
    await session.flush()


async def forget_ai_decisions(
    session: AsyncSession, workspace_id: uuid.UUID, keys: list[str] | None = None
) -> None:
    """Drop cached AI decisions (all, or only `keys`); used when the tree changes.

    The cached clean name stays: it describes the store, not a category.
    """
    stmt = (
        sa_update(MerchantMemory)
        .where(MerchantMemory.workspace_id == workspace_id)
        .values(ai_category_id=None, ai_confidence=None)
        .execution_options(synchronize_session="fetch")
    )
    if keys is None:
        await session.execute(stmt)
        return
    for start in range(0, len(keys), CHUNK):
        await session.execute(
            stmt.where(MerchantMemory.merchant_key.in_(keys[start:start + CHUNK]))
        )


async def move_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    from_category_id: uuid.UUID,
    to_category_id: uuid.UUID | None,
) -> None:
    """Re-point what the memory knows about a category that is going away.

    `counts` is JSON with no foreign key behind it, so a merged or deleted
    category would linger there. With a destination the choices move to it
    (summed with any it already had); without one they are dropped. The
    cached AI decision moves too, or is cleared (the store's clean name
    stays). Only the rows that mention the category are loaded.
    """
    old = str(from_category_id)
    rows = (
        await session.execute(
            select(MerchantMemory).where(
                MerchantMemory.workspace_id == workspace_id,
                or_(
                    MerchantMemory.last_user_category_id == from_category_id,
                    MerchantMemory.ai_category_id == from_category_id,
                    # The JSON (JSONB on Postgres) read as text: a coarse
                    # match, settled exactly below.
                    cast(MerchantMemory.counts, String).contains(old),
                ),
            )
        )
    ).scalars().all()
    for row in rows:
        counts = _counts(row.counts)
        if old in counts:
            moved = counts.pop(old)
            if to_category_id is not None:
                new = str(to_category_id)
                counts[new] = counts.get(new, 0) + moved
            row.counts = counts  # reassigned: in-place JSON mutation is not tracked
        if row.last_user_category_id == from_category_id:
            row.last_user_category_id = to_category_id
        if row.ai_category_id == from_category_id:
            row.ai_category_id = to_category_id
            if to_category_id is None:
                row.ai_confidence = None
    await session.flush()


async def repoint_store(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    key: str,
    to_category_id: uuid.UUID,
    *,
    from_ids: set[uuid.UUID] | None = None,
) -> None:
    """Move a store's recorded choices to another category.

    Used when an approved reorganization moves the store's rows (a split's
    redistribution, a new category that takes the store): otherwise the
    memory would keep filing the store's next rows where they no longer
    belong. Only the choices for `from_ids` move (all of them when None),
    summed into `to_category_id`. A store the memory never saw is left
    alone.
    """
    row = (
        await session.execute(
            select(MerchantMemory).where(
                MerchantMemory.workspace_id == workspace_id, MerchantMemory.merchant_key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    target = str(to_category_id)
    counts: dict[str, int] = {}
    for raw_id, raw_count in (row.counts or {}).items():
        try:
            count = int(raw_count or 0)
            moves = from_ids is None or uuid.UUID(raw_id) in from_ids
        except (TypeError, ValueError):
            continue
        entry = target if moves else raw_id
        counts[entry] = counts.get(entry, 0) + count
    row.counts = counts  # reassigned: in-place JSON mutation is not tracked
    last = row.last_user_category_id
    if last is not None and (from_ids is None or last in from_ids):
        row.last_user_category_id = to_category_id
    await session.flush()


async def on_user_choice(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    tx: Transaction,
    new_category_id: uuid.UUID | None,
) -> None:
    """User-choice hook: feed the memory with the person's pick."""
    if new_category_id is None:
        return
    # A savepoint, so a failing learner rolls back only its own work and
    # leaves the caller's transaction usable.
    async with session.begin_nested():
        key = stored_key(await key_for_transaction(session, tx))
        await learn_choice(session, workspace_id, key, new_category_id)


async def on_user_clear(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    tx: Transaction,
    previous_category_id: uuid.UUID,
) -> None:
    """User-clear hook (R27-U): the person took the store's category away,
    so the memory loses one pick for it and the store's cached AI decision."""
    async with session.begin_nested():
        key = stored_key(await key_for_transaction(session, tx))
        await unlearn_choice(session, workspace_id, key, previous_category_id)
        await forget_ai_decisions(session, workspace_id, [key])


async def on_bulk_choice(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    changed: list[tuple[Transaction, uuid.UUID | None]],
    new_category_id: uuid.UUID | None,
) -> None:
    """A person's bulk categorize: one pick per store, not one per row.

    `changed` holds each row whose category moved, with the category it
    held. A bulk clear takes back one pick per store and category it held,
    and drops those stores' cached AI decisions (R27-U). Like the hooks, a
    failure is logged and leaves the caller's transaction usable.
    """
    if not changed:
        return
    try:
        async with session.begin_nested():
            keys = await keys_for(session, [tx for tx, _previous in changed])
            if new_category_id is not None:
                for key in dict.fromkeys(keys.values()):
                    await learn_choice(session, workspace_id, key, new_category_id)
                return
            taken = dict.fromkeys(
                (keys[tx.id], previous) for tx, previous in changed if previous is not None
            )
            for key, previous in taken:
                await unlearn_choice(session, workspace_id, key, previous)
            await forget_ai_decisions(
                session, workspace_id, list(dict.fromkeys(key for key, _ in taken))
            )
    except Exception:
        logger.exception("bulk memory update failed in workspace %s", workspace_id)


if on_user_choice not in events.user_choice_hooks:
    events.user_choice_hooks.append(on_user_choice)
if on_user_clear not in events.user_clear_hooks:
    events.user_clear_hooks.append(on_user_clear)
