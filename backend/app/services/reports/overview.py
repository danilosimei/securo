"""The Visão geral tab: monthly KPIs, what changed, highlights and the money map.

Every number comes from the shared account rules (`rules.py`), so the tab,
the Explorar tab and the dashboard agree. The highlights are a closed list
of rules, in `highlights.py`.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category_group import CategoryGroup
from app.services.reports import highlights as highlights_service
from app.services.reports import recurring as recurring_service
from app.services.reports import repeats as repeats_service
from app.services.reports.breakdown import (
    NO_CATEGORY_LABEL,
    NO_GROUP_LABEL,
    RowRef,
    _group_level,
    _spend_by_row,
)
from app.services.reports.highlights import CHANGE_BASELINE_MONTHS, RULES, Change
from app.services.reports.income_expenses import (
    UNCATEGORIZED_KEY,
    _bucket_items,
    _category_meta,
)
from app.services.reports.periods import Period, _count_months, _month_end, _shift_months
from app.services.reports.rules import (
    CENT,
    ZERO,
    RangeTotals,
    SegmentFlows,
    collect_flows,
    load_category_kinds,
    totals_from,
)

__all__ = ["RULES", "overview"]

MAX_CHANGES = 5
SPARKLINE_MONTHS = highlights_service.SPARKLINE_MONTHS

INCOME_TO_CHECK_LABEL = "A conferir"


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _kpis(totals: RangeTotals, months: int, fixed_month: Decimal) -> dict:
    """The four monthly numbers of a period, averaged over its `months`."""
    monthly_spending = totals.spending / months
    monthly_income = (totals.income + totals.uncategorized_income) / months
    left = monthly_income - monthly_spending
    return {
        "spending": _money(monthly_spending),
        "income": _money(monthly_income),
        "left": _money(left),
        "left_share": round(float(left / monthly_income), 3) if monthly_income > 0 else None,
        "fixed_share": round(float(fixed_month / monthly_spending), 3) if monthly_spending > 0 else None,
    }


def _change_api(change: Change) -> dict:
    return {
        "kind": change.ref.kind,
        "id": change.ref.id,
        "label": change.ref.label,
        "change": change.change,
        "delta": _money(change.delta),
    }


@dataclass
class _Month:
    start: date
    segment: tuple[date, date]
    closed: bool


def _months_of(period: Period, today: date) -> list[_Month]:
    out = []
    cursor = period.from_.replace(day=1)
    while cursor <= period.to:
        end = _month_end(cursor)
        segment = (max(cursor, period.from_), min(end, period.to))
        closed = segment == (cursor, end) and end < today
        out.append(_Month(cursor, segment, closed))
        cursor = _shift_months(cursor, 1)
    return out


async def overview(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    period: Period,
    period_name: str,
    today: date,
) -> dict:
    flow_kwargs: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "primary_currency": primary_currency,
    }
    kinds = await load_category_kinds(session, workspace_id)
    row_of = await _group_level(session, workspace_id)
    months = _months_of(period, today)
    flows = await collect_flows(session, segments=[m.segment for m in months], **flow_kwargs)
    totals = totals_from(flows, kinds)
    month_totals = [totals_from([seg], kinds) for seg in flows]

    recurring = await recurring_service.detect_recurring(
        session, workspace_id=workspace_id, primary_currency=primary_currency, today=today
    )
    active_recurring = [r for r in recurring if r.status != "stopped"]
    fixed_month = sum((r.amount_month for r in active_recurring), ZERO)

    kpis = _kpis(totals, period.months, fixed_month)
    # The comparison only makes sense against a closed period.
    kpis_previous = None
    if not period.partial:
        (previous_flows,) = await collect_flows(
            session, segments=[(period.previous_from, period.previous_to)], **flow_kwargs
        )
        kpis_previous = _kpis(
            totals_from([previous_flows], kinds),
            _count_months(period.previous_from, period.previous_to),
            fixed_month,
        )

    closed_spending = [t.spending for m, t in zip(months, month_totals) if m.closed]
    median_pool = closed_spending or [t.spending for t in month_totals]
    median_spending = median(median_pool) if median_pool else ZERO
    monthly = [
        {
            "month": m.start.strftime("%Y-%m"),
            "spending": _money(t.spending),
            "income": _money(t.income + t.uncategorized_income),
            "median_spending": _money(median_spending),
        }
        for m, t in zip(months, month_totals)
    ]

    changes = await _changes(session, row_of, period, today, kinds, flow_kwargs)
    highlights, repeats = await _highlights(
        session,
        workspace_id=workspace_id,
        primary_currency=primary_currency,
        period=period,
        period_name=period_name,
        today=today,
        closed=[(m.start, t) for m, t in zip(months, month_totals) if m.closed],
        totals=totals,
        row_of=row_of,
        changes=changes,
        recurring=recurring,
        kinds=kinds,
        flow_kwargs=flow_kwargs,
    )

    return {
        "period": {
            "from": period.from_.isoformat(),
            "to": period.to.isoformat(),
            "partial": period.partial,
            "previous_from": period.previous_from.isoformat(),
            "previous_to": period.previous_to.isoformat(),
        },
        "currency": primary_currency,
        "kpis": kpis,
        "kpis_previous": kpis_previous,
        "unconverted_count": totals.unconverted_count,
        "changes": [_change_api(c) for c in changes],
        "highlights": highlights,
        **repeats,
        "monthly": monthly,
        "money_map": await _money_map(session, workspace_id, flows, kinds),
    }


async def _changes(
    session: AsyncSession,
    row_of,
    period: Period,
    today: date,
    kinds: dict,
    flow_kwargs: dict[str, Any],
) -> list[Change]:
    """Group rises and falls of the last closed month against the average of
    the 3 months before it, biggest absolute delta first."""
    end_month = period.to.replace(day=1)
    target = end_month if period.to == _month_end(period.to) and period.to < today else _shift_months(end_month, -1)
    starts = [_shift_months(target, -back) for back in range(CHANGE_BASELINE_MONTHS, -1, -1)]
    segments = [(s, _month_end(s)) for s in starts]
    flows = await collect_flows(session, segments=segments, **flow_kwargs)
    per_month = [_spend_by_row(totals_from([seg], kinds), row_of) for seg in flows]
    baseline_months, target_spend = per_month[:-1], per_month[-1]
    refs: set[RowRef] = set(target_spend)
    for month in baseline_months:
        refs.update(month)
    rows = []
    for ref in refs:
        average = sum((m.get(ref, ZERO) for m in baseline_months), ZERO) / CHANGE_BASELINE_MONTHS
        delta = (target_spend.get(ref, ZERO) - average).quantize(CENT)
        change = round(float(delta / average), 3) if average > 0 else None
        rows.append(Change(ref, target, delta, change, average))
    rows = [r for r in rows if r.delta != 0]
    rows.sort(key=lambda r: (-abs(r.delta), r.ref.label))
    return rows[:MAX_CHANGES]


async def _money_map(
    session: AsyncSession, workspace_id: uuid.UUID, flows: list[SegmentFlows], kinds: dict
) -> dict:
    """Income -> spending by group + what is left. Negatives are folded away
    as on the older Mapa do Dinheiro, so every value is positive and
    `left` is exactly income - spending of the map."""
    meta = await _category_meta(session, workspace_id)
    expense_items, income_items = _bucket_items(flows, kinds, meta)
    group_names = {
        gid: name
        for gid, name in (
            await session.execute(
                select(CategoryGroup.id, CategoryGroup.name).where(
                    CategoryGroup.workspace_id == workspace_id
                )
            )
        ).all()
    }
    refunds = sum(
        (-v for v in totals_from(flows, kinds).spend_by_category.values() if v < 0), ZERO
    )
    groups: dict[Any, dict] = {}
    for item in expense_items:
        if item.key == UNCATEGORIZED_KEY:
            slot, gid, label, cid = "none", None, NO_CATEGORY_LABEL, None
        elif item.group_id in group_names:
            slot, gid, label, cid = (
                item.group_id, str(item.group_id), group_names[item.group_id], item.key,
            )
        else:
            slot, gid, label, cid = "nogroup", None, NO_GROUP_LABEL, item.key
        entry = groups.setdefault(slot, {"id": gid, "label": label, "value": ZERO, "categories": []})
        entry["value"] += item.value
        entry["categories"].append((cid, item.label, item.value))
    group_rows = sorted(groups.values(), key=lambda g: (-g["value"], g["label"]))

    # Income is shown by category (Salário, Dividendos, Freela...), never folded
    # into its group; income without a category stays "A conferir".
    income: dict[Any, list] = {}
    for item in income_items:
        if item.key == UNCATEGORIZED_KEY:
            slot, rid, label = "none", None, INCOME_TO_CHECK_LABEL
        else:
            slot, rid, label = item.key, item.key, item.label
        entry = income.setdefault(slot, [rid, label, ZERO])
        entry[2] += item.value
    income_rows = sorted(income.values(), key=lambda e: (-e[2], e[1]))

    total_income = sum((e[2] for e in income_rows), ZERO)
    total_spending = sum((g["value"] for g in group_rows), ZERO)
    return {
        "income": [{"id": rid, "label": label, "value": _money(v)} for rid, label, v in income_rows],
        "groups": [
            {
                "id": g["id"],
                "label": g["label"],
                "value": _money(g["value"]),
                "categories": [
                    {"id": cid, "label": label, "value": _money(v)}
                    for cid, label, v in sorted(g["categories"], key=lambda c: (-c[2], c[1]))
                ],
            }
            for g in group_rows
        ],
        "refunds": _money(refunds),
        "left": _money(total_income - total_spending),
    }


async def _highlights(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    primary_currency: str,
    period: Period,
    period_name: str,
    today: date,
    closed: list,
    totals,
    row_of,
    changes: list[Change],
    recurring: list,
    kinds: dict,
    flow_kwargs: dict[str, Any],
) -> tuple[list[dict], dict]:
    """The reads the highlight rules and "O que se repete" share, then both."""
    last = period.to.replace(day=1)
    spark_starts = [_shift_months(last, -back) for back in range(SPARKLINE_MONTHS - 1, -1, -1)]
    current = today.replace(day=1)
    segments = [(s, min(_month_end(s), period.to)) for s in spark_starts]
    flows = await collect_flows(session, segments=segments, **flow_kwargs)
    spark = [(s, totals_from([seg], kinds)) for s, seg in zip(spark_starts, flows)]
    # The current month so far, whatever the period.
    (so_far,) = await collect_flows(session, segments=[(current, today)], **flow_kwargs)
    month_so_far = totals_from([so_far], kinds).spending
    first_debit = min(spark_starts[0], period.from_, _shift_months(current, -SPARKLINE_MONTHS))
    debits = await recurring_service.load_debits(
        session,
        workspace_id=workspace_id,
        primary_currency=primary_currency,
        start=first_debit,
        end=max(today, period.to),
    )
    ctx = highlights_service.Context(
        session=session,
        workspace_id=workspace_id,
        primary_currency=primary_currency,
        period=period,
        period_name=period_name,
        today=today,
        closed=closed,
        totals=totals,
        row_of=row_of,
        changes=changes,
        recurring=recurring,
        spark_months=spark,
        debits=debits,
        month_so_far=month_so_far,
    )
    return await highlights_service.build(ctx), await repeats_service.build(
        ctx, await highlights_service.category_rows(session, workspace_id)
    )
