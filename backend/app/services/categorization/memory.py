"""Per-merchant memory: what this workspace taught us about each store.

Two things live in a `MerchantMemory` row. `counts` is what the person chose
(fed only by their own, non-derived categorizations) and the memory applies a
category only when it is both repeated and dominant. The `ai_*` columns cache
the model's decision for the store so the next transaction of the same store
costs nothing; a person's choice clears that cache.

Nothing here commits: the caller owns the transaction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import MerchantMemory
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services.categorization import events
from app.services.categorization.merchant_key import merchant_key

#: Choices needed before the memory is trusted.
MIN_CHOICES = 2
#: Share of the choices the dominant category must hold.
DOMINANCE = 0.8


@dataclass(frozen=True)
class MemoryHit:
    category_id: uuid.UUID | None
    consistent: bool
    distribution: dict[str, int] = field(default_factory=dict)
    ai_category_id: uuid.UUID | None = None
    ai_confidence: int | None = None
    ai_clean_name: str | None = None


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

    Known gap: with `update_transaction(apply_to="all"/"future")`, when the
    anchor already has the chosen category and only its siblings change, no
    user hook fires (the siblings are derived), so nothing is learned.
    """
    row = await _get_or_create(session, workspace_id, key)
    counts = dict(row.counts or {})
    counts[str(category_id)] = counts.get(str(category_id), 0) + 1
    row.counts = counts  # reassigned: in-place JSON mutation is not tracked
    row.last_user_category_id = category_id
    row.ai_category_id = None
    row.ai_confidence = None
    row.ai_clean_name = None
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
        counts = {k: int(v) for k, v in (row.counts or {}).items()}
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
    """Drop cached AI decisions (all, or only `keys`); used when the tree changes."""
    stmt = (
        sa_update(MerchantMemory)
        .where(MerchantMemory.workspace_id == workspace_id)
        .values(ai_category_id=None, ai_confidence=None, ai_clean_name=None)
    )
    if keys is not None:
        stmt = stmt.where(MerchantMemory.merchant_key.in_(keys))
    await session.execute(stmt.execution_options(synchronize_session="fetch"))


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
    cached AI decision moves too, or is cleared.
    """
    old = str(from_category_id)
    rows = (
        await session.execute(
            select(MerchantMemory).where(MerchantMemory.workspace_id == workspace_id)
        )
    ).scalars().all()
    for row in rows:
        counts = dict(row.counts or {})
        if old in counts:
            moved = counts.pop(old)
            if to_category_id is not None:
                new = str(to_category_id)
                counts[new] = int(counts.get(new, 0)) + int(moved)
            row.counts = counts  # reassigned: in-place JSON mutation is not tracked
        if row.last_user_category_id == from_category_id:
            row.last_user_category_id = to_category_id
        if row.ai_category_id == from_category_id:
            row.ai_category_id = to_category_id
            if to_category_id is None:
                row.ai_confidence = None
                row.ai_clean_name = None
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
        key = await key_for_transaction(session, tx)
        await learn_choice(session, workspace_id, key, new_category_id)


if on_user_choice not in events.user_choice_hooks:
    events.user_choice_hooks.append(on_user_choice)
