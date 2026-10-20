"""Automatic categorization: review queue, audit list, undo, settings, runs.

Nothing here calls the model: runs are queued for the worker.
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.categorization import (
    AcceptRequest,
    AppliedPage,
    AppliedResult,
    CategorizationSettingsRead,
    CategorizationSettingsUpdate,
    CategorizationSummary,
    CreateCategoryRequest,
    CreateCategoryResult,
    DismissRequest,
    DismissResult,
    Provenance,
    ReviewQueue,
    RuleDraft,
    RunRead,
    RunRequest,
    SimilarRequest,
    SimilarResult,
    UndoRequest,
    UndoResultRead,
)
from app.services.categorization import events, review
from app.services.categorization import settings as categorization_settings
from app.tasks import categorization_tasks

router = APIRouter(prefix="/api/categorization", tags=["categorization"])

MAX_PAGE = 200


def _not_found(exc: review.NotFound) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _sources(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return review.AUTO_SOURCES
    wanted = tuple(dict.fromkeys(s.strip() for s in raw.split(",") if s.strip()))
    unknown = [s for s in wanted if s not in review.AUTO_SOURCES]
    if unknown or not wanted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"source must be among {', '.join(review.AUTO_SOURCES)}",
        )
    return wanted


@router.get("/summary", response_model=CategorizationSummary)
async def get_summary(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await review.summary(session, ctx.workspace.id)


@router.get("/review", response_model=ReviewQueue)
async def get_review(
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await review.review_queue(session, ctx.workspace.id, limit=limit, offset=offset)


@router.post("/review/accept", response_model=AppliedResult)
async def accept_review(
    data: AcceptRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await review.accept(
            session,
            ctx.workspace.id,
            ctx.user_id,
            merchant_key=data.merchant_key,
            category_id=data.category_id,
            transaction_ids=data.transaction_ids,
        )
    except review.NotFound as exc:
        raise _not_found(exc)
    await session.commit()
    return AppliedResult(applied=result.applied, event_ids=result.event_ids)


@router.post("/review/dismiss", response_model=DismissResult)
async def dismiss_review(
    data: DismissRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    dismissed = await review.dismiss(session, ctx.workspace.id, data.merchant_key)
    await session.commit()
    return DismissResult(dismissed=dismissed)


@router.post("/review/create-category", response_model=CreateCategoryResult)
async def create_category_from_review(
    data: CreateCategoryRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    category_id, result = await review.create_category(
        session,
        ctx.workspace.id,
        ctx.user_id,
        group=data.group,
        name=data.name,
        merchant_keys=data.merchant_keys,
    )
    await session.commit()
    return CreateCategoryResult(
        category_id=category_id, applied=result.applied, event_ids=result.event_ids
    )


@router.get("/applied", response_model=AppliedPage)
async def list_applied(
    source: Optional[str] = Query(None, description="Comma-separated: ai, memory, rule"),
    min_confidence: Optional[int] = Query(None, ge=0, le=100),
    max_confidence: Optional[int] = Query(None, ge=0, le=100),
    since: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    items, total = await review.applied_events(
        session,
        ctx.workspace.id,
        sources=_sources(source),
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        since=since,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@router.post("/undo", response_model=UndoResultRead)
async def undo_changes(
    data: UndoRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    result = await events.undo(
        session,
        ctx.workspace.id,
        event_ids=data.event_ids,
        run_id=data.run_id,
        user_id=ctx.user_id,
    )
    await session.commit()
    return UndoResultRead(restored=result.restored, skipped=result.skipped)


@router.get("/transactions/{tx_id}/provenance", response_model=Provenance)
async def get_provenance(
    tx_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await review.provenance(session, ctx.workspace.id, tx_id)
    except review.NotFound as exc:
        raise _not_found(exc)


@router.post("/similar", response_model=SimilarResult)
async def find_similar(
    data: SimilarRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        _, key, ids = await review.similar(session, ctx.workspace.id, data.transaction_id)
    except review.NotFound as exc:
        raise _not_found(exc)
    return SimilarResult(merchant_key=key, count=len(ids), transaction_ids=ids)


@router.post("/similar/apply", response_model=AppliedResult)
async def apply_similar(
    data: SimilarRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await review.apply_similar(
            session, ctx.workspace.id, ctx.user_id, data.transaction_id
        )
    except review.NotFound as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    await session.commit()
    return AppliedResult(applied=result.applied, event_ids=result.event_ids)


@router.get("/rule-draft/{tx_id}", response_model=RuleDraft)
async def get_rule_draft(
    tx_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await review.rule_draft(session, ctx.workspace.id, tx_id)
    except review.NotFound as exc:
        raise _not_found(exc)
    except review.NoSafePattern as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.get("/settings", response_model=CategorizationSettingsRead)
async def get_categorization_settings(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    settings = await categorization_settings.get_settings(session, ctx.workspace.id)
    await session.commit()
    return settings


@router.put("/settings", response_model=CategorizationSettingsRead)
async def put_categorization_settings(
    data: CategorizationSettingsUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    # Settings spend the owner's money: managing them is an owner/manager call.
    ctx.require_owner()
    try:
        settings = await categorization_settings.update_settings(session, ctx.workspace.id, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    await session.commit()
    return settings


@router.post("/run", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def run_categorization(
    response: Response,
    data: RunRequest = RunRequest(),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Queue a manual pass over the backlog; a run already busy is returned instead."""
    run, created = await review.request_run(session, ctx.workspace.id)
    await session.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
        return run
    if not categorization_tasks.enqueue_categorization(ctx.workspace.id, "manual", run_id=run.id):
        await review.mark_enqueue_failed(session, run)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível iniciar a categorização agora; tente de novo em instantes.",
        )
    return run
