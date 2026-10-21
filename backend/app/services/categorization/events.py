"""Who set a transaction's category, written down, protected and reversible.

Every write of `Transaction.category_id` goes through this module, so
`category_source` always says who put the value there and
`CategorizationEvent` keeps what it replaced:

- `record_change`: one row, one savepoint (edits, single writes);
- `stage_change` + `write_staged` / `record_changes_bulk`: many rows, the
  changes applied in memory as they happen and all their events inserted
  in one savepoint (rule runs, bulk categorize, sync and import loops);
- `record_moves`: a set-based category merge that keeps each row's origin.

A person clearing a category (`source="user"` with no category) is noted
as `none` (R27): the row is a guess again, open to review, and the event
still names the person (`user_id`). While that clear is the row's latest
standing event (`cleared_by_person`), the pipeline only suggests a
category for it, never files it on its own. The clear also runs the
user-clear hooks (R27-U), so the memory stops reinforcing the category the
person took away.

Nothing here commits: the caller owns the transaction, like
`reconciliation_history_service.record`.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert as sa_insert
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import CATEGORY_SOURCES, CategorizationEvent
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

#: A person (or a legacy import) decided: the pipeline must not touch it.
PROTECTED_SOURCES: frozenset[str] = frozenset({"user", "legacy"})
#: Guesses the pipeline may revisit.
REVIEWABLE_SOURCES: frozenset[str] = frozenset({"none", "provider", "memory", "ai"})
#: `reason` of the events `record_moves` writes. Their previous category is
#: the deleted one (nulled by the FK), so `undo` cannot restore them.
CATEGORY_MERGE_REASON = "category_merge"
#: Rows per `IN (...)` when `undo` (or `cleared_by_person`) loads what it checks.
UNDO_CHUNK = 1000


class Unset:
    """Marks "argument not passed", so None can still mean "clear it"."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()

UserChoiceHook = Callable[
    [AsyncSession, uuid.UUID, Transaction, uuid.UUID | None], Awaitable[bool | None]
]
#: Async callbacks run as `(session, workspace_id, tx, new_category_id)` when a
#: person picks a category. The memory learner registers itself here. A hook
#: returns True when it counted a pick, so the event is marked `memory_pick`.
user_choice_hooks: list[UserChoiceHook] = []

UserClearHook = Callable[
    [AsyncSession, uuid.UUID, Transaction, uuid.UUID], Awaitable[bool | None]
]
#: Async callbacks run as `(session, workspace_id, tx, previous_category_id)`
#: when a person clears a category (R27-U). The memory registers itself here.
#: A hook returns True when it took a pick back (the event is marked).
user_clear_hooks: list[UserClearHook] = []


@dataclass
class UndoResult:
    restored: int
    skipped: int


@dataclass
class StagedChange:
    """A category change already applied to `tx`, its event still unwritten."""

    tx: Transaction
    source: str
    previous_category_id: uuid.UUID | None
    previous_source: str | None
    new_category_id: uuid.UUID | None
    created_at: datetime
    user_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    reason: str | None = None
    confidence: int | None = None
    model: str | None = None
    #: Follows from another change (a sibling parcel, a transfer pair, an
    #: inherited installment): noted, but not a new choice for the hooks.
    derived: bool = False
    #: The payee before a change staged with `new_payee_id`. UNSET (no payee
    #: staged) records the row's payee at write time on both sides.
    previous_payee_id: uuid.UUID | None | Unset = UNSET


def is_protected(tx: Transaction) -> bool:
    return tx.category_source in PROTECTED_SOURCES


def _origin(source: str, new_category_id: uuid.UUID | None) -> str:
    """The origin a change leaves on the row: a person's empty choice is
    no choice to protect (R27)."""
    return "none" if source == "user" and new_category_id is None else source


def changes_category() -> Any:
    """The events that moved a row's category. The others are confirmations:
    the pipeline settling a guess at the category it already held (maybe
    setting a payee), nothing a person would check or take back."""
    return CategorizationEvent.previous_category_id.is_distinct_from(
        CategorizationEvent.new_category_id
    )


async def cleared_by_person(session: AsyncSession, tx_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """The rows of `tx_ids` whose latest standing event (not undone, not an
    undo) is a person clearing the category (R27): one query per chunk.

    Callers pass the rows that are still empty guesses (no category, `none`).
    """
    cleared: set[uuid.UUID] = set()
    for start in range(0, len(tx_ids), UNDO_CHUNK):
        ranked = (
            select(
                CategorizationEvent.transaction_id,
                CategorizationEvent.source,
                CategorizationEvent.user_id,
                CategorizationEvent.new_category_id,
                func.row_number()
                .over(
                    partition_by=CategorizationEvent.transaction_id,
                    order_by=(CategorizationEvent.created_at.desc(), CategorizationEvent.id.desc()),
                )
                .label("rank"),
            )
            .where(
                CategorizationEvent.transaction_id.in_(tx_ids[start:start + UNDO_CHUNK]),
                CategorizationEvent.undone_at.is_(None),
                CategorizationEvent.source != "undo",
            )
            .subquery()
        )
        cleared.update(
            (
                await session.execute(
                    select(ranked.c.transaction_id).where(
                        ranked.c.rank == 1,
                        ranked.c.source == "none",
                        ranked.c.user_id.is_not(None),
                        ranked.c.new_category_id.is_(None),
                    )
                )
            ).scalars().all()
        )
    return cleared


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def run_user_hooks(
    session: AsyncSession, tx: Transaction, new_category_id: uuid.UUID | None
) -> bool:
    """Run the user-choice hooks for `tx`; a failing hook is logged, never raised.

    `record_change` and `write_staged` call it for a person's own,
    non-derived choice. A caller whose choice reached only derived rows (a
    series edit that moved the siblings alone) calls it once itself.
    Returns whether a hook counted a pick: the event that carries the
    choice is then marked (`memory_pick`), so its undo takes that pick back.
    """
    counted = False
    for hook in list(user_choice_hooks):
        try:
            counted = bool(await hook(session, tx.workspace_id, tx, new_category_id)) or counted
        except Exception:
            logger.exception("on_user_choice hook failed for transaction %s", tx.id)
    return counted


def _person_clear(
    source: str,
    user_id: uuid.UUID | None,
    new_category_id: uuid.UUID | None,
    derived: bool,
) -> bool:
    """A change that is a person emptying the category (the event
    `cleared_by_person` reads), not one that follows another row's."""
    return source == "none" and user_id is not None and new_category_id is None and not derived


async def run_clear_hooks(
    session: AsyncSession, tx: Transaction, previous_category_id: uuid.UUID
) -> bool:
    """Run the user-clear hooks for `tx`; a failing hook is logged, never raised.

    Run by `record_change` and `write_staged` for a person's own,
    non-derived clear, and by callers that take a choice back once per store.
    Returns whether a hook took a pick back (the clear's event is marked).
    """
    took = False
    for hook in list(user_clear_hooks):
        try:
            took = bool(await hook(session, tx.workspace_id, tx, previous_category_id)) or took
        except Exception:
            logger.exception("on_user_clear hook failed for transaction %s", tx.id)
    return took


async def mark_picks(session: AsyncSession, event_ids: list[uuid.UUID]) -> None:
    """Mark events the memory counted one pick for (`memory_pick`)."""
    for start in range(0, len(event_ids), UNDO_CHUNK):
        await session.execute(
            sa_update(CategorizationEvent)
            .where(CategorizationEvent.id.in_(event_ids[start:start + UNDO_CHUNK]))
            .values(memory_pick=True)
            .execution_options(synchronize_session="fetch")
        )


async def mark_latest_picks(session: AsyncSession, tx_ids: list[uuid.UUID]) -> None:
    """Mark the latest standing event of each row (the one the caller just
    wrote for it) as the one the memory counted a pick for: for callers that
    run the hooks themselves, once per store, after a set-based write."""
    marked: list[uuid.UUID] = []
    for start in range(0, len(tx_ids), UNDO_CHUNK):
        ranked = (
            select(
                CategorizationEvent.id,
                func.row_number()
                .over(
                    partition_by=CategorizationEvent.transaction_id,
                    order_by=(CategorizationEvent.created_at.desc(), CategorizationEvent.id.desc()),
                )
                .label("rank"),
            )
            .where(
                CategorizationEvent.transaction_id.in_(tx_ids[start:start + UNDO_CHUNK]),
                CategorizationEvent.undone_at.is_(None),
                CategorizationEvent.source != "undo",
            )
            .subquery()
        )
        marked.extend(
            (await session.execute(select(ranked.c.id).where(ranked.c.rank == 1))).scalars().all()
        )
    await mark_picks(session, marked)


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
    derived: bool = False,
) -> CategorizationEvent | None:
    """Apply a category (and optionally payee) change and note it.

    Returns None when nothing changes. Never raises on the event's account:
    the change to `tx` is applied even if writing the event fails. A
    `derived` change (one that only follows a person's choice made on
    another row) is noted but does not run the user-choice (or user-clear)
    hooks.
    """
    if source not in CATEGORY_SOURCES:
        logger.error("invalid category source %r for transaction %s", source, tx.id)
        return None
    source = _origin(source, new_category_id)

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

    moved_pick = False
    if source == "user" and not derived:
        moved_pick = await run_user_hooks(session, tx, new_category_id)
    elif previous_category_id is not None and _person_clear(
        source, user_id, new_category_id, derived
    ):
        moved_pick = await run_clear_hooks(session, tx, previous_category_id)
    if moved_pick and event is not None:
        event.memory_pick = True
    return event


def stage_change(
    tx: Transaction,
    *,
    source: str,
    new_category_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    reason: str | None = None,
    confidence: int | None = None,
    model: str | None = None,
    derived: bool = False,
    new_payee_id: uuid.UUID | None | Unset = UNSET,
) -> StagedChange | None:
    """Apply a category (and optionally payee) change to `tx` in memory;
    `write_staged` notes it.

    Same rules as `record_change` (invalid source or no change: None), but
    no I/O, so loops over many rows pay for one insert at the end.
    """
    if source not in CATEGORY_SOURCES:
        logger.error("invalid category source %r for transaction %s", source, tx.id)
        return None
    source = _origin(source, new_category_id)
    payee_changes = not isinstance(new_payee_id, Unset) and new_payee_id != tx.payee_id
    if tx.category_id == new_category_id and tx.category_source == source and not payee_changes:
        return None
    staged = StagedChange(
        tx=tx,
        source=source,
        previous_category_id=tx.category_id,
        previous_source=tx.category_source,
        new_category_id=new_category_id,
        created_at=_now(),
        user_id=user_id,
        run_id=run_id,
        reason=reason[:120] if reason else None,
        confidence=confidence,
        model=model,
        derived=derived,
        previous_payee_id=tx.payee_id if payee_changes else UNSET,
    )
    tx.category_id = new_category_id
    tx.category_source = source
    if payee_changes and not isinstance(new_payee_id, Unset):
        tx.payee_id = new_payee_id
    return staged


async def write_staged(session: AsyncSession, staged: list[StagedChange]) -> int:
    """Insert the events of staged changes in one savepoint; returns how many.

    The rows themselves are flushed first, outside the savepoint, so a
    failing event insert (logged, never raised) cannot take them back.
    Rows must already have an id (be flushed) when staged.
    """
    if not staged:
        return 0
    await session.flush()
    event_ids = [uuid.uuid4() for _ in staged]
    payload = [
        {
            "id": event_id,
            "workspace_id": change.tx.workspace_id,
            "transaction_id": change.tx.id,
            "run_id": change.run_id,
            "source": change.source,
            "previous_source": change.previous_source,
            "previous_category_id": change.previous_category_id,
            "new_category_id": change.new_category_id,
            "previous_payee_id": (
                change.tx.payee_id
                if isinstance(change.previous_payee_id, Unset)
                else change.previous_payee_id
            ),
            "new_payee_id": change.tx.payee_id,
            "reason": change.reason,
            "confidence": change.confidence,
            "model": change.model,
            "user_id": change.user_id,
            "created_at": change.created_at,
        }
        for change, event_id in zip(staged, event_ids)
    ]
    try:
        async with session.begin_nested():
            await session.execute(sa_insert(CategorizationEvent), payload)
    except Exception:
        logger.exception("could not record %d categorization events", len(payload))
    moved: list[uuid.UUID] = []
    for change, event_id in zip(staged, event_ids):
        moved_pick = False
        if change.source == "user" and not change.derived:
            moved_pick = await run_user_hooks(session, change.tx, change.new_category_id)
        elif change.previous_category_id is not None and _person_clear(
            change.source, change.user_id, change.new_category_id, change.derived
        ):
            moved_pick = await run_clear_hooks(session, change.tx, change.previous_category_id)
        if moved_pick:
            moved.append(event_id)
    # A failed insert above leaves nothing to mark: the UPDATE matches no row.
    await mark_picks(session, moved)
    return len(staged)


async def record_changes_bulk(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    changes: list[tuple[Transaction, uuid.UUID | None, str]],
    *,
    user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    derived: bool = False,
) -> int:
    """Apply `(tx, new_category_id, source)` changes and note them in one insert.

    Returns how many rows changed. Rows outside `workspace_id` are refused.
    Callers pre-filter protected rows when the source is not a person.
    """
    staged: list[StagedChange] = []
    for tx, new_category_id, source in changes:
        if tx.workspace_id != workspace_id:
            logger.error("transaction %s is not in workspace %s", tx.id, workspace_id)
            continue
        change = stage_change(
            tx, source=source, new_category_id=new_category_id,
            user_id=user_id, run_id=run_id, derived=derived,
        )
        if change is not None:
            staged.append(change)
    return await write_staged(session, staged)


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
    """One change over many rows, by id; returns how many changed.

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
    return await record_changes_bulk(
        session,
        workspace_id,
        [(tx, new_category_id, source) for tx in result.scalars().all()],
        user_id=user_id,
        run_id=run_id,
    )


async def record_moves(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    from_category_id: uuid.UUID,
    to_category_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> int:
    """Move every transaction from one category to another, keeping its origin.

    Set-based, for taxonomy tidying (merging a category into another): one
    `UPDATE ... RETURNING` (no gap between reading the rows and moving them)
    and one bulk insert of events whose `source` is the origin each row
    already had, tagged `reason=CATEGORY_MERGE_REASON`. Returns how many rows
    moved. The move itself never fails on the events' account.

    These events are history only: the merged category is deleted right
    after, which nulls their `previous_category_id`, so `undo` skips them.
    """
    rows = (
        await session.execute(
            sa_update(Transaction)
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.category_id == from_category_id,
            )
            .values(category_id=to_category_id)
            .returning(Transaction.id, Transaction.category_source, Transaction.payee_id)
        )
    ).all()
    if not rows:
        return 0
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
            "reason": CATEGORY_MERGE_REASON,
            "user_id": user_id,
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
    changes_only: bool = False,
) -> UndoResult:
    """Revert events, unless the transaction moved on since.

    An event is skipped when a later, not-undone event exists for the same
    transaction (a person may have corrected it), when it was already undone,
    when it is itself an undo, or when it records a category merge (its
    previous category was deleted). Newest first, so chained events unwind.
    `changes_only` leaves confirmations (`changes_category`) out altogether:
    neither reverted nor counted. A confirmation left out still counts as a
    later standing event for the same row.
    """
    if event_ids is None and run_id is None:
        return UndoResult(0, 0)

    query = select(CategorizationEvent).where(CategorizationEvent.workspace_id == workspace_id)
    if event_ids is not None:
        query = query.where(CategorizationEvent.id.in_(event_ids))
    if run_id is not None:
        query = query.where(CategorizationEvent.run_id == run_id)
    if changes_only:
        query = query.where(changes_category())
    targets: list[Any] = list(
        (await session.execute(query.order_by(CategorizationEvent.created_at.desc()))).scalars().all()
    )

    # Loaded once per chunk of rows, not per event: a run may hold thousands.
    tx_ids = list(dict.fromkeys(ev.transaction_id for ev in targets))
    standing: dict[uuid.UUID, list[tuple[uuid.UUID, datetime]]] = {}
    rows: dict[uuid.UUID, Transaction] = {}
    for start in range(0, len(tx_ids), UNDO_CHUNK):
        chunk = tx_ids[start:start + UNDO_CHUNK]
        for ev_id, tx_id, created_at in (
            await session.execute(
                select(
                    CategorizationEvent.id,
                    CategorizationEvent.transaction_id,
                    CategorizationEvent.created_at,
                ).where(
                    CategorizationEvent.transaction_id.in_(chunk),
                    CategorizationEvent.undone_at.is_(None),
                    CategorizationEvent.source != "undo",
                )
            )
        ).all():
            standing.setdefault(tx_id, []).append((ev_id, _aware(created_at)))
        rows.update(
            (tx.id, tx)
            for tx in (
                await session.execute(select(Transaction).where(Transaction.id.in_(chunk)))
            ).scalars()
        )

    undone_now: set[uuid.UUID] = set()
    restored = skipped = 0
    for ev in targets:  # newest first, so chained events in one batch unwind in order
        if (
            ev.undone_at is not None
            or ev.source == "undo"
            or ev.reason == CATEGORY_MERGE_REASON
        ):
            skipped += 1
            continue
        # A later event still standing (a person may have corrected it).
        when = _aware(ev.created_at)
        if any(
            other_id != ev.id and other_id not in undone_now and created_at > when
            for other_id, created_at in standing.get(ev.transaction_id, [])
        ):
            skipped += 1
            continue
        tx = rows.get(ev.transaction_id)
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
        undone_now.add(ev.id)
        restored += 1

    await session.flush()
    return UndoResult(restored=restored, skipped=skipped)
