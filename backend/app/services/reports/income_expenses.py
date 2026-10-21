"""Income vs expenses report on the shared account rules.

Same response shape as the older report (`ReportResponse`), different
criteria: the sums come from `rules.py` (refunds ease the spending, `income`
categories are the income, money moved between own accounts is neither), the
window is closed months, the comparison is the previous period of the same
size, and nothing is projected (no recurring rules, pending rows or future
invoices), so the projected series stay at zero.

Refund choice: the Sankey and the donuts drop non-positive values, so a
category whose net spending is negative (a refund category, or a category
with more refunds than purchases) is not emitted as a negative item; it
eases the biggest spending items of its own category group first, then the
biggest ones overall. The composition therefore adds up to the expenses.
"""
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.category import Category
from app.models.user import User
from app.schemas.report import (
    CategoryTrendItem,
    ReportBreakdown,
    ReportCompositionItem,
    ReportDataPoint,
    ReportMeta,
    ReportResponse,
    ReportSummary,
)
from app.services.reports.periods import (
    Period,
    _count_months,
    _month_end,
    _month_start,
    _shift_months,
    resolve_period,
)
from app.services.reports.rules import (
    ZERO,
    SegmentFlows,
    collect_flows,
    load_category_kinds,
    totals_from,
)

CATEGORY_TREND_TOP_N = 11
UNCATEGORIZED_KEY = "uncategorized"
NEUTRAL_COLOR = "#6B7280"

#: `months=N` values that are the same window as a closed preset.
_MONTHS_PRESET = {3: "last_3m", 6: "last_6m", 12: "last_12m"}


def resolve_window(
    *,
    today: date,
    months: int = 12,
    period: Optional[str] = None,
    days: Optional[int] = None,
    financial_year_start_month: int = 1,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Period:
    """The window a request means, by precedence: explicit dates, `period`
    (closed list), `days` (rolling, up to today), `months`.

    Without `period`, `months` 3/6/12 mean `last_3m`/`last_6m`/`last_12m`
    (closed months); any other N is N closed months ending last month."""
    if start_date is not None and end_date is not None:
        return resolve_period("custom", today, start_date, end_date)
    if period is not None:
        return resolve_period(period, today, fy_start_month=financial_year_start_month)
    if days:
        start = today - timedelta(days=days - 1)
        previous_to = start - timedelta(days=1)
        return Period(
            from_=start,
            to=today,
            partial=True,
            previous_from=previous_to - timedelta(days=days - 1),
            previous_to=previous_to,
            months=_count_months(start, today),
        )
    if months in _MONTHS_PRESET:
        return resolve_period(_MONTHS_PRESET[months], today)
    start = _shift_months(_month_start(today), -months)
    end = _month_start(today) - timedelta(days=1)
    previous_from = _shift_months(start, -months)
    return Period(
        from_=start,
        to=end,
        partial=False,
        previous_from=previous_from,
        previous_to=start - timedelta(days=1),
        months=months,
    )


def _label(d: date, interval: str) -> str:
    if interval == "daily":
        return d.isoformat()
    if interval == "weekly":
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if interval == "yearly":
        return str(d.year)
    return f"{d.year}-{d.month:02d}"


def _buckets(start: date, end: date, interval: str) -> list[tuple[str, date, date]]:
    """(label, first day, last day) of each interval bucket, clipped to the window."""
    out: list[tuple[str, date, date]] = []
    cursor = start
    while cursor <= end:
        if interval == "daily":
            bucket_end = cursor
        elif interval == "weekly":
            bucket_end = cursor + timedelta(days=6 - cursor.weekday())
        elif interval == "yearly":
            bucket_end = date(cursor.year, 12, 31)
        else:
            bucket_end = _month_end(cursor)
        bucket_end = min(bucket_end, end)
        out.append((_label(cursor, interval), cursor, bucket_end))
        cursor = bucket_end + timedelta(days=1)
    return out


@dataclass
class _Meta:
    name: str
    color: str
    group_id: Optional[uuid.UUID]


@dataclass
class _Item:
    key: str
    label: str
    color: str
    group_id: Optional[uuid.UUID]
    value: Decimal


async def _category_meta(session: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, _Meta]:
    rows = await session.execute(
        select(Category.id, Category.name, Category.color, Category.group_id).where(
            Category.workspace_id == workspace_id
        )
    )
    return {cid: _Meta(name, color or NEUTRAL_COLOR, gid) for cid, name, color, gid in rows.all()}


def _fold_negatives(items: list[_Item]) -> list[_Item]:
    """Drop non-positive items, easing positive ones by what the negatives
    were worth: same category group first, then the biggest overall."""
    positives = sorted((i for i in items if i.value > 0), key=lambda i: -i.value)
    negatives = [i for i in items if i.value < 0]
    for neg in negatives:
        left = -neg.value
        same_group = [p for p in positives if neg.group_id is not None and p.group_id == neg.group_id]
        others = [p for p in positives if p not in same_group]
        for target in same_group + others:
            if left <= 0:
                break
            take = min(target.value, left)
            target.value -= take
            left -= take
    return [i for i in positives if i.value > 0]


def _bucket_items(
    flows: list[SegmentFlows], kinds: dict[uuid.UUID, str], meta: dict[uuid.UUID, _Meta]
) -> tuple[list[_Item], list[_Item]]:
    """(spending items, income items) of one or more segments, one per category."""
    totals = totals_from(flows, kinds)
    expense: list[_Item] = []
    for category_id, value in totals.spend_by_category.items():
        if category_id is None:
            expense.append(_Item(UNCATEGORIZED_KEY, "Uncategorized", NEUTRAL_COLOR, None, value))
            continue
        info = meta.get(category_id) or _Meta("Uncategorized", NEUTRAL_COLOR, None)
        expense.append(_Item(str(category_id), info.name, info.color, info.group_id, value))
    income: list[_Item] = []
    for category_id, value in totals.income_by_category.items():
        info = meta.get(category_id) or _Meta("Uncategorized", NEUTRAL_COLOR, None)
        income.append(_Item(str(category_id), info.name, info.color, info.group_id, value))
    if totals.uncategorized_income > 0:
        income.append(
            _Item(UNCATEGORIZED_KEY, "Uncategorized", NEUTRAL_COLOR, None, totals.uncategorized_income)
        )
    return _fold_negatives(expense), [i for i in income if i.value > 0]


def _composition(expense: list[_Item], income: list[_Item]) -> list[ReportCompositionItem]:
    out = []
    for group, items in (("income", income), ("expenses", expense)):
        for item in items:
            out.append(
                ReportCompositionItem(
                    key=item.key,
                    label=item.label,
                    value=round(float(item.value), 2),
                    color=item.color,
                    group=group,
                )
            )
    return out


def _f(value: Decimal) -> float:
    return round(float(value), 2)


async def get_income_expenses_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 12,
    interval: str = "monthly",
    currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    period: Optional[str] = None,
    days: Optional[int] = None,
    financial_year_start_month: int = 1,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    today: Optional[date] = None,
) -> ReportResponse:
    today = today or app_today()
    window = resolve_window(
        today=today,
        months=months,
        period=period,
        days=days,
        financial_year_start_month=financial_year_start_month,
        start_date=start_date,
        end_date=end_date,
    )
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    buckets = _buckets(window.from_, window.to, interval)
    segments: list[Any] = [(lo, hi) for _, lo, hi in buckets]
    segments.append((window.previous_from, window.previous_to))
    flows = await collect_flows(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=primary_currency,
        segments=segments,
        account_ids=account_ids,
    )
    bucket_flows, previous_flows = flows[:-1], flows[-1]
    kinds = await load_category_kinds(session, workspace_id)
    meta = await _category_meta(session, workspace_id)

    def money(seg_flows: list[SegmentFlows]) -> tuple[Decimal, Decimal]:
        totals = totals_from(seg_flows, kinds)
        return totals.income + totals.uncategorized_income, totals.spending

    trend: list[ReportDataPoint] = []
    per_bucket: list[tuple[list[_Item], list[_Item]]] = []
    for (label, _, _), seg in zip(buckets, bucket_flows):
        income, expenses = money([seg])
        expense_items, income_items = _bucket_items([seg], kinds, meta)
        per_bucket.append((expense_items, income_items))
        trend.append(
            ReportDataPoint(
                date=label,
                value=_f(income - expenses),
                breakdowns={
                    "income": _f(income),
                    "expenses": _f(expenses),
                    "projectedIncome": 0.0,
                    "projectedExpenses": 0.0,
                },
                composition=_composition(*per_bucket[-1]),
            )
        )

    total_income, total_expenses = money(bucket_flows)
    net = total_income - total_expenses
    previous_income, previous_expenses = money([previous_flows])
    previous_net = previous_income - previous_expenses
    change_amount = net - previous_net
    change_percent = (
        round(float(change_amount / abs(previous_net) * 100), 2) if previous_net != 0 else None
    )

    summary = ReportSummary(
        primary_value=_f(net),
        change_amount=_f(change_amount),
        change_percent=change_percent,
        breakdowns=[
            ReportBreakdown(key="income", label="Income", value=_f(total_income), color="#10B981"),
            ReportBreakdown(key="expenses", label="Expenses", value=_f(total_expenses), color="#F43F5E"),
            ReportBreakdown(key="projectedIncome", label="Projected Income", value=0.0, color="#34D399"),
            ReportBreakdown(key="projectedExpenses", label="Projected Expenses", value=0.0, color="#FB7185"),
            ReportBreakdown(key="netIncome", label="Net Income", value=_f(net), color="#6366F1"),
        ],
    )
    meta_out = ReportMeta(
        type="income_expenses",
        series_keys=["income", "expenses", "projectedIncome", "projectedExpenses"],
        currency=primary_currency,
        interval=interval,
    )

    composition = _composition(*_bucket_items(bucket_flows, kinds, meta))

    # Per-category series (top N + "other" per group), from the per-bucket items.
    series_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for (label, _, _), (expense_items, income_items) in zip(buckets, per_bucket):
        for group, items in (("expenses", expense_items), ("income", income_items)):
            for item in items:
                entry = series_by_key.setdefault(
                    (item.key, group),
                    {"label": item.label, "color": item.color, "total": ZERO, "periods": {}},
                )
                entry["total"] += item.value
                entry["periods"][label] = entry["periods"].get(label, ZERO) + item.value

    labels = [label for label, _, _ in buckets]

    def series_of(periods: dict[str, Decimal]) -> list[ReportDataPoint]:
        return [
            ReportDataPoint(date=lb, value=_f(periods.get(lb, ZERO)), breakdowns={}) for lb in labels
        ]

    category_trend: list[CategoryTrendItem] = []
    for group in ("expenses", "income"):
        ranked = sorted(
            ((k, v) for (k, g), v in series_by_key.items() if g == group),
            key=lambda kv: kv[1]["total"],
            reverse=True,
        )
        for key, info in ranked[:CATEGORY_TREND_TOP_N]:
            category_trend.append(
                CategoryTrendItem(
                    key=key,
                    label=info["label"],
                    color=info["color"],
                    total=_f(info["total"]),
                    group=group,
                    series=series_of(info["periods"]),
                )
            )
        rest = ranked[CATEGORY_TREND_TOP_N:]
        if rest:
            other_periods: dict[str, Decimal] = {}
            for _, info in rest:
                for lb, value in info["periods"].items():
                    other_periods[lb] = other_periods.get(lb, ZERO) + value
            category_trend.append(
                CategoryTrendItem(
                    key="other",
                    label="Other",
                    color=NEUTRAL_COLOR,
                    total=_f(sum((i["total"] for _, i in rest), ZERO)),
                    group=group,
                    series=series_of(other_periods),
                )
            )

    return ReportResponse(
        summary=summary,
        trend=trend,
        meta=meta_out,
        composition=composition,
        category_trend=category_trend,
    )
