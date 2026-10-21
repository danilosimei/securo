"""Fill and repair `transactions.merchant_key` for rows already in the database.

The model hook keeps the key right on every write through the ORM; this covers
rows older than the column, a payee renamed afterwards and payee deletes or
merges done with Core statements. Idempotent: a second run changes nothing.
"""
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.categorization.memory import keys_for

#: Rows read (and written) per batch.
BATCH = 1000


@dataclass
class BackfillReport:
    workspace_id: uuid.UUID
    scanned: int = 0
    filled: int = 0  # was null
    corrected: int = 0  # had another key

    @property
    def changed(self) -> int:
        return self.filled + self.corrected


async def backfill_workspace(
    session_maker: Any, workspace_id: uuid.UUID, *, dry_run: bool = False, batch: int = BATCH
) -> BackfillReport:
    report = BackfillReport(workspace_id)
    last_id: uuid.UUID | None = None
    while True:
        async with session_maker() as session:
            query = (
                select(Transaction)
                .where(Transaction.workspace_id == workspace_id)
                .order_by(Transaction.id)
                .limit(batch)
            )
            if last_id is not None:
                query = query.where(Transaction.id > last_id)
            txs = list((await session.execute(query)).scalars().all())
            if not txs:
                return report
            last_id = txs[-1].id
            keys = await keys_for(session, txs)
            rows = []
            for tx in txs:
                report.scanned += 1
                new = keys[tx.id]
                if tx.merchant_key == new:
                    continue
                if tx.merchant_key is None:
                    report.filled += 1
                else:
                    report.corrected += 1
                rows.append({"id": tx.id, "merchant_key": new})
            if rows and not dry_run:
                # Core update by id: bypasses the model hook on purpose.
                for row in rows:
                    await session.execute(
                        update(Transaction)
                        .where(Transaction.id == row["id"])
                        .values(merchant_key=row["merchant_key"])
                    )
                await session.commit()


async def backfill_all(
    session_maker: Any, *, dry_run: bool = False, workspace_id: uuid.UUID | None = None,
    batch: int = BATCH,
) -> list[BackfillReport]:
    async with session_maker() as session:
        query = select(Workspace.id).order_by(Workspace.id)
        if workspace_id is not None:
            query = query.where(Workspace.id == workspace_id)
        ids = list((await session.execute(query)).scalars().all())
    return [
        await backfill_workspace(session_maker, ws_id, dry_run=dry_run, batch=batch)
        for ws_id in ids
    ]
