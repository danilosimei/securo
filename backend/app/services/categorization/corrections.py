"""The person's corrections of the model, as counter-examples for the prompt.

A correction is a store where the person chose a category other than the one
the model had put or proposed. Two records say so, and both are read:

* an event with `source="user"` over `previous_source="ai"` and a different
  category: the model had applied a category and the person moved the row;
* an `accepted` suggestion whose category differs from the one the person
  then filed the row under (choosing in Revisar settles the pending
  suggestions as accepted whatever the choice, so the suggestion alone
  does not tell; the person's event does).

Events the person undid (`undone_at`) do not count. The store is the key
written on the transaction (`Transaction.merchant_key`). Most recent first,
one per store, and only the first `CORRECTIONS_LIMIT` reach the prompt.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import CategorizationEvent, CategorizationSuggestion
from app.models.transaction import Transaction
from app.services.categorization.prompts import (
    CORRECTIONS_LIMIT,
    CategoryIndex,
    build_corrections,
)

#: Rows read from each source; plenty to fill the limit after dropping repeats
#: of one store and categories that left the tree.
SCAN_LIMIT = 400


async def recent_corrections(
    session: AsyncSession, workspace_id: uuid.UUID, *, before: datetime | None = None
) -> list[tuple[str, uuid.UUID, uuid.UUID]]:
    """(merchant key, chosen category, rejected category), most recent first,
    one per store. With `before`, only corrections made earlier than that."""
    found: list[tuple[datetime, str, uuid.UUID, uuid.UUID]] = []

    moved = (
        select(
            CategorizationEvent.created_at,
            Transaction.merchant_key,
            CategorizationEvent.new_category_id,
            CategorizationEvent.previous_category_id,
        )
        .join(Transaction, Transaction.id == CategorizationEvent.transaction_id)
        .where(
            CategorizationEvent.workspace_id == workspace_id,
            CategorizationEvent.source == "user",
            CategorizationEvent.previous_source == "ai",
            CategorizationEvent.undone_at.is_(None),
            CategorizationEvent.new_category_id.is_not(None),
            CategorizationEvent.previous_category_id.is_not(None),
            CategorizationEvent.new_category_id != CategorizationEvent.previous_category_id,
            Transaction.merchant_key.is_not(None),
        )
        .order_by(CategorizationEvent.created_at.desc())
        .limit(SCAN_LIMIT)
    )
    if before is not None:
        moved = moved.where(CategorizationEvent.created_at < before)
    for created_at, key, chosen, rejected in (await session.execute(moved)).all():
        found.append((created_at, key, chosen, rejected))

    settled = (
        select(
            CategorizationSuggestion.transaction_id,
            CategorizationSuggestion.category_id,
            CategorizationSuggestion.created_at,
            Transaction.merchant_key,
        )
        .join(Transaction, Transaction.id == CategorizationSuggestion.transaction_id)
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "accepted",
            CategorizationSuggestion.category_id.is_not(None),
            Transaction.merchant_key.is_not(None),
        )
        .order_by(CategorizationSuggestion.resolved_at.desc())
        .limit(SCAN_LIMIT)
    )
    suggestions = (await session.execute(settled)).all()
    if suggestions:
        choices = await _latest_choices(
            session, workspace_id, {row[0] for row in suggestions}, before
        )
        for tx_id, suggested, suggested_at, key in suggestions:
            choice = choices.get(tx_id)
            if choice is None:
                continue
            made_at, chosen = choice
            if chosen != suggested and _naive(made_at) >= _naive(suggested_at):
                found.append((made_at, key, chosen, suggested))

    found.sort(key=lambda item: _naive(item[0]), reverse=True)
    seen: set[str] = set()
    pairs: list[tuple[str, uuid.UUID, uuid.UUID]] = []
    for _, key, chosen, rejected in found:
        if key in seen:
            continue
        seen.add(key)
        pairs.append((key, chosen, rejected))
    return pairs


async def _latest_choices(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transaction_ids: set[uuid.UUID],
    before: datetime | None,
) -> dict[uuid.UUID, tuple[datetime, uuid.UUID]]:
    """Transaction -> (when, category) of the person's latest standing choice."""
    ids = list(transaction_ids)
    choices: dict[uuid.UUID, tuple[datetime, uuid.UUID]] = {}
    for start in range(0, len(ids), 500):
        query = (
            select(
                CategorizationEvent.transaction_id,
                CategorizationEvent.created_at,
                CategorizationEvent.new_category_id,
            )
            .where(
                CategorizationEvent.workspace_id == workspace_id,
                CategorizationEvent.transaction_id.in_(ids[start:start + 500]),
                CategorizationEvent.source == "user",
                CategorizationEvent.undone_at.is_(None),
                CategorizationEvent.new_category_id.is_not(None),
            )
            .order_by(CategorizationEvent.created_at.desc())
        )
        if before is not None:
            query = query.where(CategorizationEvent.created_at < before)
        for tx_id, created_at, category_id in (await session.execute(query)).all():
            choices.setdefault(tx_id, (created_at, category_id))
    return choices


def _naive(moment: datetime) -> datetime:
    """SQLite hands datetimes back without a zone, Postgres with one."""
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


async def load_corrections(
    session: AsyncSession, workspace_id: uuid.UUID, index: CategoryIndex
) -> str:
    """The prompt's corrections block text for the workspace (may be empty)."""
    pairs = await recent_corrections(session, workspace_id)
    return build_corrections(pairs, index, CORRECTIONS_LIMIT)
