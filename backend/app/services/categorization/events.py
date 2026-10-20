"""Who set a transaction's category, written down, protected and reversible.

Every write of `Transaction.category_id` goes through `record_change`,
`record_bulk` or (for a set-based category merge) `record_moves`, so `category_source` always says who put the value there and
`CategorizationEvent` keeps what it replaced. Nothing here commits: the
caller owns the transaction, like `reconciliation_history_service.record`.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import CATEGORY_SOURCES, CategorizationEvent
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

#: A person (or a legacy import) decided: the pipeline must not touch it.
PROTECTED_SOURCES: frozenset[str] = frozenset({"user", "legacy"})
#: Guesses the pipeline may revisit.
REVIEWABLE_SOURCES: frozenset[str] = frozenset({"none", "provider", "memory", "ai"})


class Unset:
    """Marks "argument not passed", so None can still mean "clear it"."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()

UserChoiceHook = Callable[[AsyncSession, uuid.UUID, Transaction, uuid.UUID | None], Awaitable[None]]
#: Async callbacks run as `(session, workspace_id, tx, new_category_id)` when a
#: person picks a category. The memory learner registers itself here.
user_choice_hooks: list[UserChoiceHook] = []


@dataclass
class UndoResult:
    restored: int
    skipped: int


def is_protected(tx: Transaction) -> bool:
    return tx.category_source in PROTECTED_SOURCES


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _run_user_hooks(
    session: AsyncSession, tx: Transaction, new_category_id: uuid.UUID | None
) -> None:
    for hook in list(user_choice_hooks):
        try:
            await hook(session, tx.workspace_id, tx, new_category_id)
        except Exception:
            logger.exception("on_user_choice hook failed for transaction %s", tx.id)


async def record_change(
    session: AsyncSession,
    tx: Transaction,
    *,
    source: str,
    new_category_id: uuid.UUID | None,
    new_payee_id: uuid.UUID | None | Unset = UNSET,
    confidence: int | None = None,
    reason: str | None = None,
    model: str | None = None,
    run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> CategorizationEvent | None:
    """Apply a category (and optionally payee) change and note it.

    Returns None when nothing changes. Never raises on the event's account:
    the change to `tx` is applied even if writing the event fails.
    """
    if source not in CATEGORY_SOURCES:
        logger.error("invalid category source %r for transaction %s", source, tx.id)
        return None

    payee_changes = not isinstance(new_payee_id, Unset) and new_payee_id != tx.payee_id
    if tx.category_id == new_category_id and tx.category_source == source and not payee_changes:
        return None

    previous_category_id = tx.category_id
    previous_source = tx.category_source
    previous_payee_id = tx.payee_id

    tx.category_id = new_category_id
    tx.category_source = source
    if not isinstance(new_payee_id, Unset):
        tx.payee_id = new_payee_id

    event: CategorizationEvent | None = None
    try:
        # A savepoint, so a failing insert (CHECK/FK) rolls back only the
        # event and leaves the caller's transaction usable.
        async with session.begin_nested():
            event = CategorizationEvent(
                id=uuid.uuid4(),
                workspace_id=tx.workspace_id,
                transaction_id=tx.id,
                run_id=run_id,
                source=source,
                previous_source=previous_source,
                previous_category_id=previous_category_id,
                new_category_id=new_category_id,
                previous_payee_id=previous_payee_id,
                new_payee_id=tx.payee_id,
                confidence=confidence,
                reason=reason[:120] if reason else None,
                model=model,
                user_id=user_id,
                created_at=_now(),
            )
            session.add(event)
            await session.flush()
    except Exception:
        logger.exception("could not record categorization event for %s", tx.id)
        event = None

    if source == "user":
        await _run_user_hooks(session, tx, new_category_id)
    return event


async def record_bulk(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    tx_ids: list[uuid.UUID],
    *,
    source: str,
    new_category_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    run_id: uuid.UUID | None = None,
) -> int:
    """`record_change` over many rows; returns how many changed.

    Callers must pre-filter protected rows (`is_protected`) when the source
    is not a person: this function writes whatever it is given.
    """
    if not tx_ids:
        return 0
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id, Transaction.id.in_(tx_ids)
        )
    )
    changed = 0
    for tx in result.scalars().all():
        event = await record_change(
            session,
            tx,
            source=source,
            new_category_id=new_category_id,
            run_id=run_id,
            user_id=user_id,
        )
        if event is not None:
            changed += 1
    return changed


async def record_moves(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    from_category_id: uuid.UUID,
    to_category_id: uuid.UUID,
) -> int:
    """Move every transaction from one category to another, keeping its origin.

    Set-based, for taxonomy tidying (merging a category into another): one
    SELECT of the rows and their current origin, one UPDATE, one bulk insert
    of events whose `source` is the origin each row already had. Returns how
    many rows moved. The move itself never fails on the events' account.
    """
    rows = (
        await session.execute(
            select(Transaction.id, Transaction.category_source, Transaction.payee_id).where(
                Transaction.workspace_id == workspace_id,
                Transaction.category_id == from_category_id,
            )
        )
    ).all()
    if not rows:
        return 0
    await session.execute(
        sa_update(Transaction)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.category_id == from_category_id,
        )
        .values(category_id=to_category_id)
    )
    now = _now()
    payload = [
        {
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "transaction_id": row.id,
            "source": row.category_source or "none",
            "previous_source": row.category_source,
            "previous_category_id": from_category_id,
            "new_category_id": to_category_id,
            "previous_payee_id": row.payee_id,
            "new_payee_id": row.payee_id,
            "created_at": now,
        }
        for row in rows
    ]
    try:
        async with session.begin_nested():
            await session.execute(sa_insert(CategorizationEvent), payload)
    except Exception:
        logger.exception("could not record category move events in workspace %s", workspace_id)
    return len(rows)


async def undo(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    event_ids: list[uuid.UUID] | None = None,
    run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None,
) -> UndoResult:
    """Revert events, unless the transaction moved on since.

    An event is skipped when a later, not-undone event exists for the same
    transaction (a person may have corrected it), when it was already undone,
    or when it is itself an undo. Newest first, so chained events unwind.
    """
    if event_ids is None and run_id is None:
        return UndoResult(0, 0)

    query = select(CategorizationEvent).where(CategorizationEvent.workspace_id == workspace_id)
    if event_ids is not None:
        query = query.where(CategorizationEvent.id.in_(event_ids))
    if run_id is not None:
        query = query.where(CategorizationEvent.run_id == run_id)
    targets: list[Any] = list(
        (await session.execute(query.order_by(CategorizationEvent.created_at.desc()))).scalars().all()
    )

    restored = skipped = 0
    for ev in targets:  # newest first, so chained events in one batch unwind in order
        if ev.undone_at is not None or ev.source == "undo":
            skipped += 1
            continue
        later = (
            await session.execute(
                select(CategorizationEvent.id).where(
                    CategorizationEvent.transaction_id == ev.transaction_id,
                    CategorizationEvent.id != ev.id,
                    CategorizationEvent.undone_at.is_(None),
                    CategorizationEvent.source != "undo",
                    CategorizationEvent.created_at > ev.created_at,
                )
            )
        ).scalars().all()
        if later:
            skipped += 1
            continue
        tx = await session.get(Transaction, ev.transaction_id)
        if tx is None or tx.workspace_id != workspace_id:
            skipped += 1
            continue

        payee_changed = ev.previous_payee_id != ev.new_payee_id
        restored_source = ev.previous_source or "none"
        session.add(
            CategorizationEvent(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                transaction_id=tx.id,
                run_id=None,
                source="undo",
                previous_source=tx.category_source,
                previous_category_id=tx.category_id,
                new_category_id=ev.previous_category_id,
                previous_payee_id=tx.payee_id,
                new_payee_id=ev.previous_payee_id if payee_changed else tx.payee_id,
                user_id=user_id,
                created_at=_now(),
            )
        )
        tx.category_id = ev.previous_category_id
        tx.category_source = restored_source
        if payee_changed:
            tx.payee_id = ev.previous_payee_id
        ev.undone_at = _now()
        restored += 1

    await session.flush()
    return UndoResult(restored=restored, skipped=skipped)
