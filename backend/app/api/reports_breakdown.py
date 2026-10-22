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
from app.services.reports import recurring as recurring_service
from app.services.reports.periods import resolve_period

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
    kind: Literal["group", "category", "merchant"]
    total: str
    share: Optional[float]
    previous_total: str
    change: Optional[float]
    count: int
    sparkline: list[str]


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
    level: Literal["group", "category", "merchant"] = Query("group"),
    period: Literal[
        "this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom"
    ] = Query("last_12m"),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    group_id: str | None = Query(None, description="Group uuid, or 'none' for categories without a group"),
    category_id: str | None = Query(None, description="Category uuid, or 'none' for rows without a category"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    group_uuid, no_group = _scope_id("group_id", group_id)
    category_uuid, no_category = _scope_id("category_id", category_id)
    resolved = resolve_period(
        period,
        app_today(),
        from_=from_,
        to=to,
        fy_start_month=_financial_year_start_month(ctx.workspace.tax_jurisdiction),
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
        category_id=category_uuid,
        no_category=no_category,
    )


class RecurringRow(BaseModel):
    merchant_key: str
    label: str
    category: str
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
