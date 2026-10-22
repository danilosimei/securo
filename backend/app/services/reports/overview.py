"""The Visão geral tab: monthly KPIs, what changed, highlights and the money map.

Every number comes from the shared account rules (`rules.py`), so the tab,
the Explorar tab and the dashboard agree. Highlights are a closed list of
rules, each with a limit (constants below) and each firing at most once.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import CategorizationSuggestion
from app.models.category_group import CategoryGroup
from app.services.reports import recurring as recurring_service
from app.services.reports.breakdown import NO_CATEGORY_LABEL, NO_GROUP_LABEL, RowRef, _group_level, _spend_by_row
from app.services.reports.income_expenses import (
    UNCATEGORIZED_KEY,
    _bucket_items,
    _category_meta,
)
from app.services.reports.periods import Period, _month_end, _shift_months
from app.services.reports.rules import (
    CENT,
    ZERO,
    SegmentFlows,
    collect_flows,
    load_category_kinds,
    totals_from,
)

#: The closed list of highlight rules.
RULES = (
    "spike",
    "what_changed",
    "recurring_new_or_up",
    "recurring_expensive",
    "small_purchases",
    "heavy_group",
    "to_review",
)

MAX_HIGHLIGHTS = 6
MAX_CHANGES = 5
CHANGE_BASELINE_MONTHS = 3

#: spike: a closed month with spending at least this many times the median.
SPIKE_MULTIPLE = Decimal("1.5")
#: spike needs a few closed months for a median to mean something.
SPIKE_MIN_CLOSED_MONTHS = 3
SPIKE_TOP_GROUPS = 3
#: what_changed: a group's rise over the 3-month average, in money and in share.
WHAT_CHANGED_MIN_DELTA = Decimal("500")
WHAT_CHANGED_MIN_SHARE = Decimal("0.30")
#: recurring_new_or_up: at most this many stores in the card.
RECURRING_CARD_MAX_ITEMS = 3
#: recurring_expensive: a fixed cost worth this much per year.
RECURRING_EXPENSIVE_MIN_YEAR = Decimal("3000")
#: small_purchases: purchases below this value, adding up to this much per month.
SMALL_PURCHASE_LIMIT = Decimal("50")
SMALL_PURCHASES_MIN_MONTH = Decimal("1000")
#: heavy_group: a group above this share of the period's spending.
HEAVY_GROUP_MIN_SHARE = Decimal("0.05")

INCOME_TO_CHECK_LABEL = "A conferir"
_MONTHS_PT = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _month_name(month: date) -> str:
    return f"{_MONTHS_PT[month.month - 1]}/{month.year % 100:02d}"


def _number_pt(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def _brl(value: Decimal) -> str:
    """Short money for titles: R$ 786, R$ 4,2 mil, R$ 54 mil, R$ 1,3 mi."""
    amount = abs(value)
    if amount < 1000:
        return f"R$ {int(amount.quantize(Decimal('1')))}"
    if amount < 10_000:
        return f"R$ {_number_pt(amount / 1000, 1)} mil"
    if amount < 1_000_000:
        return f"R$ {int((amount / 1000).quantize(Decimal('1')))} mil"
    return f"R$ {_number_pt(amount / 1_000_000, 1)} mi"


@dataclass
class _Change:
    ref: RowRef
    target: date
    delta: Decimal
    change: Optional[float]
    average: Decimal

    def api(self) -> dict:
        return {
            "kind": self.ref.kind,
            "id": self.ref.id,
            "label": self.ref.label,
            "change": self.change,
            "delta": _money(self.delta),
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


def _link(period_name: str, from_: Optional[date], to: Optional[date], **extra: Optional[str]) -> dict:
    link: dict[str, Any] = {"tab": "explore", "period": period_name}
    if period_name == "custom" and from_ and to:
        link["from"] = from_.isoformat()
        link["to"] = to.isoformat()
    link.update({k: v for k, v in extra.items() if v is not None})
    return link


def _card(rule: str, title: str, value: Decimal | int, compare: str, link: dict) -> dict:
    return {
        "rule": rule,
        "title": title,
        "value": _money(value) if isinstance(value, Decimal) else str(value),
        "compare": compare,
        "link": link,
    }


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

    spending = totals.spending
    income = totals.income + totals.uncategorized_income
    monthly_spending = spending / period.months
    monthly_income = income / period.months
    left = monthly_income - monthly_spending

    recurring = await recurring_service.detect_recurring(
        session, workspace_id=workspace_id, primary_currency=primary_currency, today=today
    )
    active_recurring = [r for r in recurring if r.status != "stopped"]
    fixed_month = sum((r.amount_month for r in active_recurring), ZERO)

    kpis = {
        "spending": _money(monthly_spending),
        "income": _money(monthly_income),
        "left": _money(left),
        "left_share": round(float(left / monthly_income), 3) if monthly_income > 0 else None,
        "fixed_share": round(float(fixed_month / monthly_spending), 3) if monthly_spending > 0 else None,
    }

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
    highlights = await _highlights(
        session,
        workspace_id=workspace_id,
        primary_currency=primary_currency,
        period=period,
        period_name=period_name,
        today=today,
        months=months,
        month_totals=month_totals,
        totals=totals,
        row_of=row_of,
        changes=changes,
        recurring=recurring,
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
        "changes": [c.api() for c in changes],
        "highlights": highlights,
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
) -> list[_Change]:
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
        rows.append(_Change(ref, target, delta, change, average))
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

    # Income is shown by the group its categories sit in ("Salário & Renda").
    income: dict[Any, list] = {}
    for item in income_items:
        if item.key == UNCATEGORIZED_KEY:
            slot, rid, label = "none", None, INCOME_TO_CHECK_LABEL
        elif item.group_id in group_names:
            slot, rid, label = item.group_id, str(item.group_id), group_names[item.group_id]
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
    months: list[_Month],
    month_totals: list,
    totals,
    row_of,
    changes: list[_Change],
    recurring: list,
) -> list[dict]:
    def link(**extra: Optional[str]) -> dict:
        return _link(period_name, period.from_, period.to, **extra)

    cards: list[dict] = []

    # 1. spike: a closed month well above the median month.
    closed = [(m, t) for m, t in zip(months, month_totals) if m.closed]
    if len(closed) >= SPIKE_MIN_CLOSED_MONTHS:
        center = median([t.spending for _, t in closed])
        if center > 0:
            month, top = max(closed, key=lambda mt: mt[1].spending)
            if top.spending >= SPIKE_MULTIPLE * center:
                per_group = [_spend_by_row(t, row_of) for _, t in closed]
                this_month = _spend_by_row(top, row_of)
                diffs = []
                for ref, value in this_month.items():
                    usual = median([g.get(ref, ZERO) for g in per_group])
                    if value - usual > 0:
                        diffs.append((value - usual, ref.label))
                diffs.sort(key=lambda d: (-d[0], d[1]))
                explain = ", ".join(
                    f"{label} +{_brl(diff)}" for diff, label in diffs[:SPIKE_TOP_GROUPS]
                )
                ratio = top.spending / center
                cards.append(
                    _card(
                        "spike",
                        f"{_month_name(month.start)} foi {_number_pt(ratio, 1)}× a mediana",
                        top.spending,
                        explain,
                        # Opens the month that spiked, not the whole period.
                        _link("custom", *month.segment),
                    )
                )

    # 2. what_changed: the group with the biggest rise in the last closed month.
    rises = [
        c
        for c in changes
        if c.ref.kind == "group"
        and c.ref.id is not None
        and c.delta >= WHAT_CHANGED_MIN_DELTA
        and c.average > 0
        and c.change is not None
        and Decimal(str(c.change)) >= WHAT_CHANGED_MIN_SHARE
    ]
    if rises:
        top_change = max(rises, key=lambda c: c.delta)
        ref = top_change.ref
        cards.append(
            _card(
                "what_changed",
                f"{ref.label} subiu {_brl(top_change.delta)} em {_month_name(top_change.target)}",
                top_change.delta,
                f"+{round((top_change.change or 0) * 100)}% sobre a média dos 3 meses anteriores",
                link(group_id=ref.id),
            )
        )

    # 3. recurring_new_or_up: stores that are new or got more expensive.
    moving = [r for r in recurring if r.status in ("new", "up")][:RECURRING_CARD_MAX_ITEMS]
    if moving:
        names = await _labels(session, workspace_id, [r.merchant_key for r in moving])
        parts = []
        for r in moving:
            note = "novo" if r.status == "new" else f"+{round(float(r.up_share or 0) * 100)}%"
            parts.append(f"{names[r.merchant_key]} ({note})")
        year = sum((r.amount_year for r in moving), ZERO)
        extra = {}
        if len(moving) == 1:
            extra = {
                "merchant": moving[0].merchant_key,
                "category_id": str(moving[0].category_id) if moving[0].category_id else None,
            }
        cards.append(
            _card(
                "recurring_new_or_up",
                f"{len(moving)} gasto{'s' if len(moving) > 1 else ''} fixo{'s' if len(moving) > 1 else ''} "
                f"novo{'s' if len(moving) > 1 else ''} ou mais caro{'s' if len(moving) > 1 else ''}: "
                f"{_brl(year)} por ano",
                year,
                ", ".join(parts),
                link(**extra),
            )
        )

    # 4. recurring_expensive: the fixed cost that weighs most in a year.
    active = [r for r in recurring if r.status != "stopped"]
    if active and active[0].amount_year >= RECURRING_EXPENSIVE_MIN_YEAR:
        top_fixed = active[0]
        names = await _labels(session, workspace_id, [top_fixed.merchant_key])
        cards.append(
            _card(
                "recurring_expensive",
                f"{names[top_fixed.merchant_key]} custa {_brl(top_fixed.amount_year)} por ano",
                top_fixed.amount_year,
                f"{_brl(top_fixed.amount_month)} por mês",
                link(
                    merchant=top_fixed.merchant_key,
                    category_id=str(top_fixed.category_id) if top_fixed.category_id else None,
                ),
            )
        )

    # 5. small_purchases: many small debits that add up.
    small = [
        value
        for _, _, day, value, _ in await recurring_service.load_debits(
            session,
            workspace_id=workspace_id,
            primary_currency=primary_currency,
            start=period.from_,
            end=period.to,
        )
        if value < SMALL_PURCHASE_LIMIT
    ]
    small_month = sum(small, ZERO) / period.months
    if small_month >= SMALL_PURCHASES_MIN_MONTH:
        cards.append(
            _card(
                "small_purchases",
                f"{len(small)} compras abaixo de {_brl(SMALL_PURCHASE_LIMIT)} somam {_brl(small_month)} por mês",
                small_month,
                f"{_brl(small_month * 12)} por ano",
                link(),
            )
        )

    # 6. heavy_group: the group that costs most per year, among those above the share.
    total_spending = totals.spending
    by_group = _spend_by_row(totals, row_of)
    heavy = [
        (value, ref)
        for ref, value in by_group.items()
        if ref.kind == "group"
        and ref.id is not None
        and total_spending > 0
        and value / total_spending > HEAVY_GROUP_MIN_SHARE
    ]
    if heavy:
        value, ref = max(heavy, key=lambda h: (h[0], h[1].label))
        yearly = value / period.months * 12
        cards.append(
            _card(
                "heavy_group",
                f"{ref.label} custa {_brl(yearly)} por ano",
                yearly,
                f"{round(float(value / total_spending) * 100)}% do gasto",
                link(group_id=ref.id),
            )
        )

    # 7. to_review: things waiting for a look, in a single card.
    pending = (
        await session.execute(
            select(func.count(CategorizationSuggestion.id)).where(
                CategorizationSuggestion.workspace_id == workspace_id,
                CategorizationSuggestion.status == "pending",
            )
        )
    ).scalar_one()
    parts = []
    count = 0
    if pending:
        count += pending
        parts.append(f"{pending} sugest{'ões' if pending > 1 else 'ão'} na Revisar")
    if totals.uncategorized_income > 0:
        count += 1
        parts.append(f"{_brl(totals.uncategorized_income)} de renda sem categoria")
    odd = [r for r in recurring if r.atypical_amount is not None]
    if odd:
        names = await _labels(session, workspace_id, [r.merchant_key for r in odd])
        for r in odd:
            count += 1
            parts.append(f"{names[r.merchant_key]} cobrou {_brl(r.atypical_amount or ZERO)}, fora do normal")
    to_review = None
    if parts:
        to_review = _card(
            "to_review",
            f"{count} {'item' if count == 1 else 'itens'} para revisar",
            count,
            " · ".join(parts),
            link(),
        )

    # Biggest value first; the review card is never the one cut.
    cards.sort(key=lambda c: (-Decimal(c["value"]), c["rule"]))
    if to_review is not None:
        return cards[: MAX_HIGHLIGHTS - 1] + [to_review]
    return cards[:MAX_HIGHLIGHTS]


async def _labels(
    session: AsyncSession, workspace_id: uuid.UUID, keys: list[str]
) -> dict[str, str]:
    from app.services.reports.breakdown import _store_names
    from app.services.reports.merchant_labels import readable_label

    names = await _store_names(session, workspace_id, set(keys))
    return {key: names.get(key) or readable_label(key) for key in keys}
