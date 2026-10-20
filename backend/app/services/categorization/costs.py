"""What categorization costs: the month's spend against the monthly cap.

Runs are the ledger (`CategorizationRun.cost_usd`); a month is the
workspace's calendar month, so the cap resets at its local midnight.
Nothing here commits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import get_workspace_timezone
from app.models.categorization import CategorizationRun


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of `now`'s month, in `now`'s timezone, as UTC."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def month_spend(session: AsyncSession, workspace_id: uuid.UUID) -> Decimal:
    """US$ spent by this workspace's runs in the current (local) month."""
    tz = await get_workspace_timezone(session, workspace_id)
    start, end = _month_bounds(datetime.now(tz))
    total = await session.scalar(
        select(func.coalesce(func.sum(CategorizationRun.cost_usd), 0)).where(
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.created_at >= start,
            CategorizationRun.created_at < end,
        )
    )
    return Decimal(str(total or 0))
