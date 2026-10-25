"""Histórico: every category change, one line per action.

An action is what one click (or one run) changed, tied by
`CategorizationEvent.action_id`. A line says who did it, from which
categories to which, in which stores, and how much of it can still be
undone: an event stands while it is not undone and nothing changed its row
since (the rule `events.undo` applies). Undos and category merges are lines
too; neither can be undone from here.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.categorization import CategorizationEvent
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.categorization.events import CATEGORY_MERGE_REASON, changes_category
from app.services.categorization.review import NotFound, standing_event

Who = Literal["you", "ai", "auto"]
#: Names, stores shown per line; the rest are only counted.
SHOWN = 3

_Event = CategorizationEvent


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _undoable() -> Any:
    """An event `events.undo` would still revert. A run is undone as a run:
    its confirmations (same category) stay, as "Desfazer este lote" does."""
    return and_(
        standing_event(),
        _Event.source != "undo",
        or_(_Event.reason.is_(None), _Event.reason != CATEGORY_MERGE_REASON),
        or_(_Event.run_id.is_(None), changes_category()),
    )


def _by_person() -> Any:
    return or_(_Event.user_id.is_not(None), _Event.source == "user")


def _how_many(condition: Any) -> Any:
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _any(condition: Any) -> Any:
    return _how_many(condition) > 0


def _who(row: Any) -> str:
    if row.undos:
        return "undo"
    if row.merges:
        return "merge"
    if row.person:
        return "you"
    if row.ai:
        return "ai"
    if row.memory == row.count:
        return "memory"
    if row.rule == row.count:
        return "rule"
    return "auto"


async def actions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    who: Optional[Who] = None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """A page of lines, newest first, and how many lines there are."""
    # Portable aggregates (no bool_or / FILTER): the tests run on SQLite.
    person = _any(_by_person())
    ai = _any(_Event.source == "ai")
    lines = (
        select(
            _Event.action_id.label("action_id"),
            func.max(_Event.created_at).label("at"),
            func.count().label("count"),
            _how_many(_undoable()).label("standing"),
            func.count(_Event.undone_at).label("undone"),
            func.max(_Event.undone_at).label("undone_at"),
            person.label("person"),
            ai.label("ai"),
            _how_many(_Event.source == "undo").label("undos"),
            _how_many(_Event.reason == CATEGORY_MERGE_REASON).label("merges"),
            _how_many(_Event.source == "memory").label("memory"),
            _how_many(_Event.source == "rule").label("rule"),
        )
        .where(_Event.workspace_id == workspace_id)
        .group_by(_Event.action_id)
    )
    if who == "you":
        lines = lines.having(person)
    elif who == "ai":
        lines = lines.having(and_(~person, ai))
    elif who == "auto":
        lines = lines.having(and_(~person, ~ai))
    total = await session.scalar(select(func.count()).select_from(lines.subquery())) or 0
    page = (
        await session.execute(
            lines.order_by(func.max(_Event.created_at).desc(), _Event.action_id).limit(limit).offset(offset)
        )
    ).all()
    if not page:
        return [], total

    details = await _details(session, workspace_id, [row.action_id for row in page])
    items = []
    for row in page:
        detail = details.get(row.action_id, {})
        items.append(
            {
                "action_id": row.action_id,
                "at": _utc(row.at),
                "who": _who(row),
                "count": row.count,
                "standing": row.standing,
                # Every event went back: the line as a whole is undone.
                "undone_at": _utc(row.undone_at) if row.undone_at and row.undone == row.count else None,
                **detail,
            }
        )
    return items, total


async def _details(
    session: AsyncSession, workspace_id: uuid.UUID, action_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Categories and stores of each line, the most frequent first."""
    rows = (
        await session.execute(
            select(
                _Event.action_id,
                _Event.previous_category_id,
                _Event.new_category_id,
                Transaction.merchant_key,
                Transaction.description,
            )
            .join(Transaction, Transaction.id == _Event.transaction_id)
            .where(_Event.workspace_id == workspace_id, _Event.action_id.in_(action_ids))
        )
    ).all()
    category_ids = {cid for r in rows for cid in (r.previous_category_id, r.new_category_id) if cid}
    names = await _category_names(session, category_ids)

    counted: dict[uuid.UUID, tuple[Counter, Counter, Counter]] = {}
    for r in rows:
        before, after, stores = counted.setdefault(r.action_id, (Counter(), Counter(), Counter()))
        before[names.get(r.previous_category_id) if r.previous_category_id else None] += 1
        after[names.get(r.new_category_id) if r.new_category_id else None] += 1
        stores[r.merchant_key or f"\0{r.description}"] += 1

    keys = {k for _, _, stores in counted.values() for k in stores if not k.startswith("\0")}
    labels = await _store_labels(session, workspace_id, keys)
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for action_id, (before, after, stores) in counted.items():
        out[action_id] = {
            "from_categories": [name for name, _ in before.most_common(SHOWN)],
            "from_total": len(before),
            "to_categories": [name for name, _ in after.most_common(SHOWN)],
            "to_total": len(after),
            "stores": [
                key[1:] if key.startswith("\0") else labels.get(key, key)
                for key, _ in stores.most_common(SHOWN)
            ],
            "stores_total": len(stores),
        }
    return out


async def _category_names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(Category.id, Category.name).where(Category.id.in_(ids)))
    return {cid: name for cid, name in rows.all()}


async def _store_labels(session: AsyncSession, workspace_id: uuid.UUID, keys: set[str]) -> dict[str, str]:
    from app.services.reports.highlights import store_labels

    return await store_labels(session, workspace_id, sorted(keys)) if keys else {}


async def action_rows(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    action_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """The rows one line changed, newest purchase first."""
    total = await session.scalar(
        select(func.count()).where(_Event.workspace_id == workspace_id, _Event.action_id == action_id)
    )
    if not total:
        raise NotFound(f"action {action_id} not found")
    before, after = aliased(Category), aliased(Category)
    rows = (
        await session.execute(
            select(
                _Event.id,
                _Event.transaction_id,
                _Event.undone_at,
                _undoable().label("standing"),
                Transaction.date,
                Transaction.description,
                Transaction.amount,
                Transaction.currency,
                Transaction.type,
                before.name.label("from_category"),
                after.name.label("to_category"),
            )
            .join(Transaction, Transaction.id == _Event.transaction_id)
            .outerjoin(before, before.id == _Event.previous_category_id)
            .outerjoin(after, after.id == _Event.new_category_id)
            .where(_Event.workspace_id == workspace_id, _Event.action_id == action_id)
            .order_by(Transaction.date.desc(), _Event.created_at.desc(), _Event.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {
            "event_id": r.id,
            "transaction_id": r.transaction_id,
            "date": r.date,
            "description": r.description,
            "amount": r.amount,
            "type": r.type,
            "currency": r.currency,
            "from_category": r.from_category,
            "to_category": r.to_category,
            "undone_at": _utc(r.undone_at) if r.undone_at else None,
            "standing": bool(r.standing),
        }
        for r in rows
    ], total

