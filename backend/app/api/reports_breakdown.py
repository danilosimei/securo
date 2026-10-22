import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports import _financial_year_start_month
from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.services.reports import breakdown as breakdown_service
from app.services.reports import habits as habits_service
from app.services.reports import overview as overview_service
from app.services.reports import recurring as recurring_service
from app.services.reports.periods import compare_period, resolve_period

router = APIRouter(prefix="/api/reports", tags=["reports"])


class BreakdownPeriod(BaseModel):
    # Serialized as "from"; `from` is a Python keyword.
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    partial: bool
    previous_from: date
    previous_to: date


class BreakdownRow(BaseModel):
    id: Optional[str]
    label: str
    kind: Literal["group", "category", "merchant", "account"]
    total: str
    share: Optional[float]
    previous_total: str
    change: Optional[float]
    count: int
    sparkline: list[str]
    #: Only on `level=category&group_id=all`.
    group_label: Optional[str] = None
    #: Only on `level=merchant&category_id=all`: the category where the store
    #: weighs most (null = no category).
    category_id: Optional[str] = None
    category_label: Optional[str] = None


class BreakdownResponse(BaseModel):
    period: BreakdownPeriod
    currency: str
    total: str
    previous_total: str
    rows: list[BreakdownRow]
    unconverted_count: int


def _scope_id(name: str, raw: str | None) -> tuple[uuid.UUID | None, bool]:
    """(uuid, False), (None, True) for the literal 'none', or (None, False)."""
    if raw is None:
        return None, False
    if raw == "none":
        return None, True
    try:
        return uuid.UUID(raw), False
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_id", "message": f"{name} must be a uuid or 'none'"},
        )


@router.get("/breakdown", response_model=BreakdownResponse)
async def get_breakdown(
    level: Literal["group", "category", "merchant", "account"] = Query("group"),
    period: Literal[
        "this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom"
    ] = Query("last_12m"),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    group_id: str | None = Query(None, description="Group uuid, 'none' for categories without a group, or 'all' (category level) for every category"),
    category_id: str | None = Query(None, description="Category uuid, 'none' for rows without a category, or 'all' (store level) for every store"),
    account_id: uuid.UUID | None = Query(None, description="Only the movements of this account"),
    compare: Literal["previous", "year_ago", "custom"] = Query(
        "previous", description="What the period is compared with"
    ),
    compare_from: date | None = Query(None, description="Start of the comparison (compare=custom)"),
    compare_to: date | None = Query(None, description="End of the comparison (compare=custom)"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    all_groups = group_id == "all"
    group_uuid, no_group = (None, False) if all_groups else _scope_id("group_id", group_id)
    all_categories = category_id == "all"
    category_uuid, no_category = (
        (None, False) if all_categories else _scope_id("category_id", category_id)
    )
    resolved = compare_period(
        resolve_period(
            period,
            app_today(),
            from_=from_,
            to=to,
            fy_start_month=_financial_year_start_month(ctx.workspace.tax_jurisdiction),
        ),
        compare,
        compare_from,
        compare_to,
    )
    return await breakdown_service.breakdown(
        session,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        primary_currency=ctx.user.primary_currency,
        period=resolved,
        level=level,
        group_id=group_uuid,
        no_group=no_group,
        all_groups=all_groups,
        category_id=category_uuid,
        no_category=no_category,
        all_categories=all_categories,
        account_id=account_id,
    )


class OverviewPeriod(BreakdownPeriod):
    pass


class OverviewKpis(BaseModel):
    spending: str
    income: str
    left: str
    left_share: Optional[float]
    fixed_share: Optional[float]


class OverviewChange(BaseModel):
    kind: Literal["group", "category"]
    id: Optional[str]
    label: str
    change: Optional[float]
    delta: str


class OverviewHighlight(BaseModel):
    rule: Literal[
        "spike",
        "what_changed",
        "recurring_new_or_up",
        "recurring_expensive",
        "small_purchases",
        "heavy_group",
        "to_review",
    ]
    title: str
    value: str
    compare: str
    #: `tab`, `period` and only the filters that apply (`from`/`to` for a
    #: custom period, `group_id`, `category_id`, `merchant`).
    link: dict[str, str]


class OverviewMonth(BaseModel):
    month: str
    spending: str
    income: str
    median_spending: str


class MoneyMapIncome(BaseModel):
    id: Optional[str]
    label: str
    value: str


class MoneyMapCategory(BaseModel):
    id: Optional[str]
    label: str
    value: str


class MoneyMapGroup(BaseModel):
    id: Optional[str]
    label: str
    value: str
    categories: list[MoneyMapCategory]


class MoneyMap(BaseModel):
    income: list[MoneyMapIncome]
    groups: list[MoneyMapGroup]
    refunds: str
    left: str


class OverviewResponse(BaseModel):
    period: OverviewPeriod
    currency: str
    kpis: OverviewKpis
    changes: list[OverviewChange]
    highlights: list[OverviewHighlight]
    monthly: list[OverviewMonth]
    money_map: MoneyMap


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    period: Literal[
        "this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom"
    ] = Query("last_12m"),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    today = app_today()
    resolved = resolve_period(
        period,
        today,
        from_=from_,
        to=to,
        fy_start_month=_financial_year_start_month(ctx.workspace.tax_jurisdiction),
    )
    return await overview_service.overview(
        session,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        primary_currency=ctx.user.primary_currency,
        period=resolved,
        period_name=period,
        today=today,
    )


class HabitMerchant(BaseModel):
    id: str
    category_id: Optional[str]
    label: str
    count: int
    per_month: float
    avg_ticket: str
    total: str
    category: str


class HabitWeekday(BaseModel):
    weekday: int
    label: str
    total: str
    count: int


class HabitBand(BaseModel):
    label: str
    min: str
    max: Optional[str]
    count: int
    total: str


class HabitDay(BaseModel):
    date: date
    total: str


class HabitsResponse(BaseModel):
    period: BreakdownPeriod
    merchants: list[HabitMerchant]
    weekdays: list[HabitWeekday]
    bands: list[HabitBand]
    days: list[HabitDay]


@router.get("/habits", response_model=HabitsResponse)
async def get_habits(
    period: Literal[
        "this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom"
    ] = Query("last_12m"),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    resolved = resolve_period(
        period,
        app_today(),
        from_=from_,
        to=to,
        fy_start_month=_financial_year_start_month(ctx.workspace.tax_jurisdiction),
    )
    return await habits_service.habits(
        session,
        workspace_id=ctx.workspace.id,
        primary_currency=ctx.user.primary_currency,
        period=resolved,
    )


class RecurringRow(BaseModel):
    merchant_key: str
    label: str
    category: str
    #: The most common category of the charges; null = uncategorized.
    category_id: Optional[str]
    amount_month: str
    amount_year: str
    status: Literal["new", "up", "stopped", "steady"]
    last_date: date
    months_seen: int


@router.get("/recurring", response_model=list[RecurringRow])
async def get_recurring(
    # Accepted for URL parity with the other tabs; the detection always reads
    # the last 12 closed months plus the current one.
    period: Literal[
        "this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom"
    ] = Query("last_12m"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await recurring_service.recurring_report(
        session,
        workspace_id=ctx.workspace.id,
        primary_currency=ctx.user.primary_currency,
        today=app_today(),
    )
