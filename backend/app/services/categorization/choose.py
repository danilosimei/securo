"""One choice for a store: an existing category or a new one, applied.

Every "this store is X" the person makes outside the plain accept button
lands here: picking another category, creating one on the spot, accepting
what the conversation proposed. The category is resolved (or created, see
`creation`), applied as the person's choice, the memory learns one pick per
store, and the store's note is saved when the request carries one.

Two scopes, by where the action came from:

* Revisar (no `transaction_ids`): the store's pending rows, as
  `review.accept` files them -- a row categorized by hand since keeps that
  choice. A store with no pending row left open (all accepted, or all
  categorized by hand since) is `review.NotFound`.
* Aplicadas or a selection (`transaction_ids`): exactly those rows, every
  one of them, a person's earlier choice included: this is the person's
  explicit word about those rows. Their pending suggestions are settled.

Nothing here commits: the API owns the transaction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import CategorizationSuggestion
from app.models.transaction import Transaction
from app.services.categorization import creation, events, memory, review
from app.services.categorization.events import PROTECTED_SOURCES, UNSET, Unset
from app.services.categorization.pipeline import CHUNK


@dataclass
class ChooseOutcome:
    category_id: uuid.UUID
    created_group: bool
    created_category: bool
    applied: review.Applied


async def _load_rows(
    session: AsyncSession, workspace_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[Transaction]:
    found: list[Transaction] = []
    for start in range(0, len(ids), CHUNK):
        result = await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id,
                Transaction.id.in_(ids[start:start + CHUNK]),
            )
        )
        found.extend(result.scalars().all())
    return found


async def _accept_pending_of_rows(
    session: AsyncSession, workspace_id: uuid.UUID, tx_ids: list[uuid.UUID]
) -> None:
    """Settle the pending suggestions over `tx_ids` as accepted."""
    now = datetime.now(timezone.utc)
    for start in range(0, len(tx_ids), CHUNK):
        await session.execute(
            sa_update(CategorizationSuggestion)
            .where(
                CategorizationSuggestion.workspace_id == workspace_id,
                CategorizationSuggestion.status == "pending",
                CategorizationSuggestion.transaction_id.in_(tx_ids[start:start + CHUNK]),
            )
            .values(status="accepted", resolved_at=now)
            .execution_options(synchronize_session="fetch")
        )


async def _has_open_pending(session: AsyncSession, workspace_id: uuid.UUID, key: str) -> bool:
    """Whether the store still has a pending row nobody categorized by hand
    since (what Revisar shows for it)."""
    found = await session.scalar(
        select(CategorizationSuggestion.id)
        .join(
            Transaction,
            and_(
                Transaction.id == CategorizationSuggestion.transaction_id,
                Transaction.workspace_id == workspace_id,
                Transaction.category_source.not_in(PROTECTED_SOURCES),
            ),
        )
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "pending",
            CategorizationSuggestion.merchant_key == memory.stored_key(key),
        )
        .limit(1)
    )
    return found is not None


async def choose(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    merchant_key: str,
    category_id: uuid.UUID | None,
    new_category: tuple[str, str] | None,
    transaction_ids: list[uuid.UUID] | None,
    note: str | None | Unset = UNSET,
) -> ChooseOutcome:
    """Apply `category_id`, or the `(group, name)` found or created, to the
    store's rows; `note` (when given; None clears it) is saved on the store.

    Exactly one of `category_id` and `new_category`: ValueError otherwise.
    Rows are checked before anything is created, so a request with nothing
    to apply to (`review.NotFound`) leaves the tree as it was.
    """
    if (category_id is None) == (new_category is None):
        raise ValueError("Informe exatamente uma: categoria existente ou nova")

    txs = None
    if transaction_ids is not None:
        txs = await _load_rows(session, workspace_id, list(dict.fromkeys(transaction_ids)))
        if not txs:
            raise review.NotFound("Transactions not found")
    elif not await _has_open_pending(session, workspace_id, merchant_key):
        raise review.NotFound("Nothing pending for this store")

    created_group = created_category = False
    if category_id is not None:
        await review.ensure_assignable(session, workspace_id, category_id)
        target = category_id
    else:
        assert new_category is not None
        group, name = new_category
        created = await creation.find_or_create_category(
            session, workspace_id, user_id, group=group, name=name
        )
        target = created.category_id
        created_group, created_category = created.created_group, created.created_category

    if txs is None:
        applied = await review.accept(
            session, workspace_id, user_id, merchant_key=merchant_key, category_id=target
        )
    else:
        applied = await _apply_to_rows(session, workspace_id, user_id, txs, target)

    if not isinstance(note, Unset):
        await memory.set_note(session, workspace_id, merchant_key, note)
    return ChooseOutcome(
        category_id=target,
        created_group=created_group,
        created_category=created_category,
        applied=applied,
    )


async def _apply_to_rows(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    txs: list[Transaction],
    category_id: uuid.UUID,
) -> review.Applied:
    """File `txs` under `category_id` as the person; one pick per store.

    A store teaches the memory only when one of its rows actually changed:
    that row's event carries the pick (`memory_pick`), so undoing the
    change, whole or row by row, takes back exactly that one.
    """
    result = await review.apply_as_user(session, workspace_id, user_id, txs, category_id)
    keys = await memory.keys_for(session, txs)
    carriers: dict[str, uuid.UUID] = {}
    for tx in txs:
        event_id = result.by_tx.get(tx.id)
        if event_id is not None:
            carriers.setdefault(keys[tx.id], event_id)
    for key in carriers:
        await memory.learn_choice(session, workspace_id, key, category_id)
    await events.mark_picks(session, list(carriers.values()))
    await _accept_pending_of_rows(session, workspace_id, [tx.id for tx in txs])
    return result
