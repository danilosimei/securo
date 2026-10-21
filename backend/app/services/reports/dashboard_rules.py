"""The dashboard's income/expenses numbers on the shared report rules.

Month income and expenses, the monthly trend and the spending by category
come from `rules.py`, so the dashboard agrees with Relatórios and Explorar:
primary currency, refunds ease the spending, `income` categories are the
income (credits with no category count too, as "income to check") and money
moved between own accounts is neither.

Spending by category folds a negative category (a refund category, or one with
more refunds than purchases) into the biggest spending of its own category
group, like the income/expenses report does, so the list has no negative line
and still adds up to the expenses card.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.category import Category
from app.models.user import User
from app.schemas.dashboard import MonthlyTrend, SpendingByCategory
from app.services.reports.income_expenses import UNCATEGORIZED_KEY, _fold_negatives, _Item
from app.services.reports.periods import _month_end, _month_start, _shift_months
from app.services.reports.rules import ZERO, collect_flows, load_category_kinds, totals_from

NO_CATEGORY_NAME = "Sem categoria"
NO_CATEGORY_ICON = "circle-help"
NEUTRAL_COLOR = "#6B7280"


def _window(month: date, today: date) -> tuple[date, date]:
    """The month's days that already happened (inclusive); empty when it is ahead."""
    start = _month_start(month)
    return start, min(_month_end(month), today)


async def month_income_expenses(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    month: date,
    today: date,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> tuple[Decimal, Decimal]:
    """(income, expenses) of the month so far, by the shared rules."""
    start, end = _window(month, today)
    if end < start:
        return ZERO, ZERO
    flows = await collect_flows(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=primary_currency,
        segments=[(start, end)],
        account_ids=account_ids,
    )
    totals = totals_from(flows, await load_category_kinds(session, workspace_id))
    return totals.income + totals.uncategorized_income, totals.spending


async def monthly_trend(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    months: int,
    today: date,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> list[MonthlyTrend]:
    """Income and expenses of the last `months` months (the current one so
    far), oldest first. Months with no movement are left out."""
    current = _month_start(today)
    segments = [
        (_shift_months(current, -back), min(_month_end(_shift_months(current, -back)), today))
        for back in range(months - 1, -1, -1)
    ]
    flows = await collect_flows(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=primary_currency,
        segments=segments,
        account_ids=account_ids,
    )
    kinds = await load_category_kinds(session, workspace_id)
    out: list[MonthlyTrend] = []
    for (start, _), seg in zip(segments, flows):
        if not seg.by_category and not seg.unconverted_count:
            continue
        totals = totals_from([seg], kinds)
        out.append(
            MonthlyTrend(
                month=f"{start.year:04d}-{start.month:02d}",
                income=float(totals.income + totals.uncategorized_income),
                expenses=float(totals.spending),
            )
        )
    return out


async def spending_by_category(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    month: date,
    today: date,
    account_ids: Optional[list[uuid.UUID]] = None,
    forecast_by_category: Optional[dict[Optional[str], float]] = None,
) -> list[SpendingByCategory]:
    """Net spending per category of the month so far, biggest first.

    `forecast_by_category` is the forecast (projected minus actual) each
    category already carried; it only rides along in `projected_total`."""
    forecast = forecast_by_category or {}
    start, end = _window(month, today)
    totals_spend: dict[Optional[uuid.UUID], Decimal] = {}
    if end >= start:
        flows = await collect_flows(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            primary_currency=primary_currency,
            segments=[(start, end)],
            account_ids=account_ids,
        )
        totals = totals_from(flows, await load_category_kinds(session, workspace_id))
        totals_spend = dict(totals.spend_by_category)

    rows = await session.execute(
        select(Category.id, Category.name, Category.icon, Category.color, Category.group_id).where(
            Category.workspace_id == workspace_id
        )
    )
    info = {cid: (name, icon, color, gid) for cid, name, icon, color, gid in rows.all()}

    items: list[_Item] = []
    for category_id, value in totals_spend.items():
        if category_id is None:
            items.append(_Item(UNCATEGORIZED_KEY, NO_CATEGORY_NAME, NEUTRAL_COLOR, None, value))
            continue
        name, _, color, gid = info.get(category_id, (NO_CATEGORY_NAME, None, None, None))
        items.append(_Item(str(category_id), name, color or NEUTRAL_COLOR, gid, value))
    folded = _fold_negatives(items)
    by_key = {item.key: item for item in folded}
    # A category that only has forecast still shows, with a zero actual.
    for key in forecast:
        wire = UNCATEGORIZED_KEY if key is None else key
        if wire in by_key:
            continue
        if key is None:
            by_key[wire] = _Item(wire, NO_CATEGORY_NAME, NEUTRAL_COLOR, None, ZERO)
        else:
            name, _, color, gid = info.get(uuid.UUID(key), (NO_CATEGORY_NAME, None, None, None))
            by_key[wire] = _Item(wire, name, color or NEUTRAL_COLOR, gid, ZERO)

    grand_total = sum((item.value for item in by_key.values()), ZERO)
    out: list[SpendingByCategory] = []
    for item in sorted(by_key.values(), key=lambda i: i.value, reverse=True):
        category_id = None if item.key == UNCATEGORIZED_KEY else item.key
        icon = NO_CATEGORY_ICON
        if category_id is not None:
            icon = info.get(uuid.UUID(category_id), (None, None))[1] or NO_CATEGORY_ICON
        total = float(item.value)
        out.append(
            SpendingByCategory(
                category_id=category_id,
                category_name=item.label,
                category_icon=icon,
                category_color=item.color,
                total=total,
                projected_total=total + forecast.get(category_id, 0.0),
                percentage=(total / float(grand_total) * 100) if grand_total else 0,
            )
        )
    return out


async def primary_currency_of(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    return user.primary_currency if user else get_settings().default_currency


async def get_monthly_trend(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 6,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> list[MonthlyTrend]:
    return await monthly_trend(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=await primary_currency_of(session, user_id),
        months=months,
        today=app_today(),
        account_ids=account_ids,
    )


async def get_spending_by_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> list[SpendingByCategory]:
    """Actuals by the shared rules; the forecast each category carried stays
    the dashboard's own (recurring rules, pending and future rows)."""
    from app.services import dashboard_service  # circular at import time

    today = app_today()
    month = month or today.replace(day=1)
    legacy = await dashboard_service.get_spending_by_category(
        session, workspace_id, user_id, month, account_ids
    )
    forecast = {
        entry.category_id: entry.projected_total - entry.total
        for entry in legacy
        if entry.projected_total - entry.total > 0.005
    }
    return await spending_by_category(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=await primary_currency_of(session, user_id),
        month=month,
        today=today,
        account_ids=account_ids,
        forecast_by_category=forecast,
    )
