"""What categorization costs: per-call pricing and the month's spend against the cap.

`cost_usd` prices one Claude response. Runs are the ledger
(`CategorizationRun.cost_usd`); a month is the workspace's calendar month,
so the cap resets at its local midnight. Nothing here commits.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import get_workspace_timezone
from app.models.categorization import CategorizationRun

logger = logging.getLogger(__name__)

_MTOK = Decimal(1_000_000)
_CACHE_WRITE_FACTOR = Decimal("1.25")
_BATCH_FACTOR = Decimal("0.5")
_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    """US$ per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal


PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(Decimal("0.10"), Decimal("0.50"), Decimal("0.01")),
    "claude-opus-5-5": ModelPrice(Decimal("4.00"), Decimal("20.00"), Decimal("0.20")),
    # Models the organizer's server-side fallback may answer with.
    "claude-opus-5": ModelPrice(Decimal("5.00"), Decimal("25.00"), Decimal("0.50")),
    "claude-opus-4-8": ModelPrice(Decimal("5.00"), Decimal("25.00"), Decimal("0.50")),
}


def priciest_opus() -> str:
    """The known Opus model with the highest rates: what an unknown served
    model is priced as, so a fallback is never under-counted."""
    opus = [name for name in PRICES if "opus" in name]
    return max(opus, key=lambda name: (PRICES[name].output, PRICES[name].input, name))


def cost_usd(model: str, usage, *, batch: bool) -> Decimal:
    """US$ for one response's `usage`; Batches cost half of everything.

    Cache writes cost 1.25x the input price. Missing counters count as 0 and
    an unknown model costs 0 (logged), so a new model never breaks a run.
    """
    price = PRICES.get(model)
    if price is None:
        logger.warning("No price for model %r; counting its cost as 0", model)
        return Decimal(0)

    def tokens(name: str) -> Decimal:
        return Decimal(getattr(usage, name, None) or 0)

    total = (
        tokens("input_tokens") * price.input
        + tokens("output_tokens") * price.output
        + tokens("cache_creation_input_tokens") * _CACHE_WRITE_FACTOR * price.input
        + tokens("cache_read_input_tokens") * price.cache_read
    ) / _MTOK
    if batch:
        total *= _BATCH_FACTOR
    return total.quantize(_QUANTUM)


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
