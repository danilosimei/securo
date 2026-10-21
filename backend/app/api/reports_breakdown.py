import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports import _financial_year_start_month
from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.services.reports import breakdown as breakdown_service
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


@router.get("/breakdown", response_model=BreakdownResponse)
async def get_breakdown(
    level: Literal["group", "category", "merchant"] = Query("group"),
    period: Literal[
        "this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom"
    ] = Query("last_12m"),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    group_id: uuid.UUID | None = Query(None),
    category_id: uuid.UUID | None = Query(None),
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
    return await breakdown_service.breakdown(
        session,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        primary_currency=ctx.user.primary_currency,
        period=resolved,
        level=level,
        group_id=group_id,
        category_id=category_id,
    )
