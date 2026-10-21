"""Spending broken down by group (and, next, by category and store).

The levels share everything but the row a category falls into, so a level is
one function from "category" to "row"; the sums, the comparison period and
the sparkline are the same for all of them.
"""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.services.reports.periods import Period, _month_end, _shift_months
from app.services.reports.rules import (
    CENT,
    ZERO,
    RangeTotals,
    SegmentFlows,
    collect_flows,
    load_category_kinds,
    totals_from,
)

LEVELS = ("group", "category", "merchant")
SPARKLINE_MONTHS = 12

# The two rows a level can need that no real group/category stands for.
# Fixed pt-BR labels, as the dashboard does for its "Sem categoria".
NO_GROUP_LABEL = "Sem grupo"
NO_CATEGORY_LABEL = "Sem categoria"


@dataclass(frozen=True)
class RowRef:
    id: Optional[str]
    label: str
    kind: str


#: category id (None = no category) -> the row its spending lands in.
RowOf = Callable[[Optional[uuid.UUID]], RowRef]


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _level_not_available(level: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "level_not_available",
            "message": f"The breakdown level '{level}' is not available yet",
        },
    )


async def _group_level(session: AsyncSession, workspace_id: uuid.UUID) -> RowOf:
    categories = {
        cid: gid
        for cid, gid in (
            await session.execute(
                select(Category.id, Category.group_id).where(Category.workspace_id == workspace_id)
            )
        ).all()
    }
    groups = {
        gid: name
        for gid, name in (
            await session.execute(
                select(CategoryGroup.id, CategoryGroup.name).where(
                    CategoryGroup.workspace_id == workspace_id
                )
            )
        ).all()
    }

    def row_of(category_id: Optional[uuid.UUID]) -> RowRef:
        if category_id is None:
            return RowRef(None, NO_CATEGORY_LABEL, "group")
        group_id = categories.get(category_id)
        if group_id is None or group_id not in groups:
            return RowRef(None, NO_GROUP_LABEL, "group")
        return RowRef(str(group_id), groups[group_id], "group")

    return row_of


def _spend_by_row(totals: RangeTotals, row_of: RowOf) -> dict[RowRef, Decimal]:
    out: dict[RowRef, Decimal] = {}
    for category_id, value in totals.spend_by_category.items():
        ref = row_of(category_id)
        out[ref] = out.get(ref, ZERO) + value
    return out


def _count_by_row(totals: RangeTotals, row_of: RowOf) -> dict[RowRef, int]:
    out: dict[RowRef, int] = {}
    for category_id, count in totals.count_by_category.items():
        if category_id in totals.spend_by_category:  # income categories are not spending
            ref = row_of(category_id)
            out[ref] = out.get(ref, 0) + count
    return out


def _month_segments(period: Period) -> list[tuple]:
    last_month = period.to.replace(day=1)
    out = []
    for back in range(SPARKLINE_MONTHS - 1, -1, -1):
        start = _shift_months(last_month, -back)
        out.append((start, min(_month_end(start), period.to)))
    return out


async def breakdown(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    period: Period,
    level: str,
    group_id: Optional[uuid.UUID] = None,
    category_id: Optional[uuid.UUID] = None,
) -> dict:
    if level != "group":
        # Category and store levels arrive in the next slice, filtered by
        # `group_id` / `category_id` and grouped by another column.
        raise _level_not_available(level)
    row_of = await _group_level(session, workspace_id)
    kinds = await load_category_kinds(session, workspace_id)

    current_flows, previous_flows = await collect_flows(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=primary_currency,
        segments=[(period.from_, period.to), (period.previous_from, period.previous_to)],
    )
    current = totals_from([current_flows], kinds)
    previous = totals_from([previous_flows], kinds)
    monthly: list[SegmentFlows] = await collect_flows(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=primary_currency,
        segments=_month_segments(period),
    )
    monthly_by_row = [_spend_by_row(totals_from([seg], kinds), row_of) for seg in monthly]

    totals_by_row = _spend_by_row(current, row_of)
    previous_by_row = _spend_by_row(previous, row_of)
    counts = _count_by_row(current, row_of)
    total = sum(totals_by_row.values(), ZERO)
    previous_total = sum(previous_by_row.values(), ZERO)

    ranked: list[tuple[Decimal, str, dict]] = []
    for ref in counts:
        row_total = totals_by_row.get(ref, ZERO)
        row_previous = previous_by_row.get(ref, ZERO)
        ranked.append(
            (
                row_total,
                ref.label,
                {
                    "id": ref.id,
                    "label": ref.label,
                    "kind": ref.kind,
                    "total": _money(row_total),
                    "share": (
                        round(float(row_total / total), 3) if total > 0 and row_total >= 0 else None
                    ),
                    "previous_total": _money(row_previous),
                    "change": (
                        round(float((row_total - row_previous) / row_previous), 3)
                        if row_previous != 0
                        else None
                    ),
                    "count": counts[ref],
                    "sparkline": [_money(month.get(ref, ZERO)) for month in monthly_by_row],
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    rows = [item[2] for item in ranked]

    return {
        "period": {
            "from": period.from_.isoformat(),
            "to": period.to.isoformat(),
            "partial": period.partial,
            "previous_from": period.previous_from.isoformat(),
            "previous_to": period.previous_to.isoformat(),
        },
        "currency": primary_currency,
        "total": _money(total),
        "previous_total": _money(previous_total),
        "rows": rows,
        "unconverted_count": current.unconverted_count,
    }
