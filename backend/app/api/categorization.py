"""Automatic categorization: review queue, audit list, undo, settings, runs.

Nothing here calls the model -- runs are queued for the worker -- except
`POST /ask`, the conversation about one store, where the person waits for
the answer (bounded per message, per conversation and per minute).
"""
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
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
    AskRequest,
    AskResponse,
    AskSuggestionRead,
    CategorizationSettingsRead,
    CategorizationSettingsUpdate,
    CategorizationSummary,
    ChooseRequest,
    ChooseResult,
    CreateCategoryRequest,
    CreateCategoryResult,
    DismissRequest,
    DismissResult,
    NoteRead,
    NoteRequest,
    OrganizerApplyResult,
    OrganizerOp,
    OrganizerRead,
    Provenance,
    ReviewQueue,
    RuleDraft,
    RunRead,
    RunRequest,
    SimilarRequest,
    SimilarResult,
    SimilarTransaction,
    UndoRequest,
    UndoResultRead,
)
from app.models.categorization import CategorizationRun
from app.services.categorization import ask, choose, creation, memory, organizer, review
from app.services.categorization.events import UNSET
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
    summary = await review.summary(session, ctx.workspace.id)
    # The first read creates the settings row (like GET /settings): keep it,
    # rather than inserting and rolling it back on every poll.
    await session.commit()
    return summary


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


@router.post("/choose", response_model=ChooseResult)
async def choose_category(
    data: ChooseRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """File a store's rows under an existing category or one created here
    (a same-named visible one is reused), and save its note when sent."""
    try:
        outcome = await choose.choose(
            session,
            ctx.workspace.id,
            ctx.user_id,
            merchant_key=data.merchant_key,
            category_id=data.category_id,
            new_category=(
                (data.new_category.group, data.new_category.name)
                if data.new_category is not None
                else None
            ),
            transaction_ids=data.transaction_ids,
            note=data.note if "note" in data.model_fields_set else UNSET,
        )
    except review.NotFound as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": str(exc)},
        )
    except (choose.InvalidChoice, creation.InvalidName) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_choice", "message": str(exc)},
        )
    await session.commit()
    return ChooseResult(
        category_id=outcome.category_id,
        created_group=outcome.created_group,
        created_category=outcome.created_category,
        applied=outcome.applied.applied,
        event_ids=outcome.applied.event_ids,
    )


# --- the conversation about a store ---------------------------------------------

#: Status and pt-BR message of each reason a message got no answer.
_ASK_ERRORS: dict[str, tuple[int, str]] = {
    "conversation_limit": (
        status.HTTP_409_CONFLICT,
        f"Esta conversa chegou ao limite de {ask.MAX_PERSON_MESSAGES} mensagens; "
        "feche e abra de novo para recomeçar.",
    ),
    "capped": (
        status.HTTP_409_CONFLICT,
        "O teto mensal de gastos com a IA foi atingido; ajuste-o nas configurações.",
    ),
    "disabled": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "A categorização com IA está desligada nas configurações.",
    ),
    "no_connection": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Não há uma conexão com a Anthropic que funcione; confira a chave nas configurações.",
    ),
    "timeout": (
        status.HTTP_504_GATEWAY_TIMEOUT,
        "A IA demorou demais para responder; tente de novo.",
    ),
    "model_error": (
        status.HTTP_502_BAD_GATEWAY,
        "A IA não conseguiu responder agora; tente de novo em instantes.",
    ),
}


async def ask_rate_limit(ctx: WorkspaceContext = Depends(current_writable_workspace)) -> None:
    """`ask.RATE_LIMIT` messages per workspace (not per IP: the money is the
    workspace's)."""
    try:
        await rate_limit.hit(f"rate_limit:categorization-ask:{ctx.workspace.id}", *ask.RATE_LIMIT)
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": "rate_limited",
                "message": "Muitas mensagens em pouco tempo; espere um minuto e tente de novo.",
            },
            headers=exc.headers,
        ) from exc


@router.post(
    "/ask", response_model=AskResponse, dependencies=[Depends(ask_rate_limit)]
)
async def ask_about_store(
    data: AskRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Answer the person's last message about a store; nothing is applied.

    Every error commits first: a failed call is still a run in the ledger
    the monthly cap reads.
    """
    try:
        answer = await ask.ask(
            session,
            ctx.workspace.id,
            merchant_key=data.merchant_key,
            transaction_ids=data.transaction_ids,
            turns=[(m.role, m.content) for m in data.messages],
        )
    except ask.AskError as exc:
        await session.commit()
        code, message = _ASK_ERRORS.get(
            exc.code, (status.HTTP_502_BAD_GATEWAY, _ASK_ERRORS["model_error"][1])
        )
        raise HTTPException(status_code=code, detail={"code": exc.code, "message": message})
    except review.NotFound:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "not_found",
                "message": "Nenhuma transação desta loja para conversar; atualize a página.",
            },
        )
    except ask.InvalidConversation:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_conversation",
                "message": "A última mensagem da conversa precisa ser sua.",
            },
        )
    await session.commit()
    suggestion = answer.suggestion
    return AskResponse(
        reply=answer.reply,
        suggestion=(
            AskSuggestionRead.model_validate(asdict(suggestion)) if suggestion is not None else None
        ),
        note=answer.note,
        assistant_turn=answer.assistant_turn,
        person_messages_left=answer.person_messages_left,
    )


@router.put("/notes", response_model=NoteRead)
async def put_note(
    data: NoteRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    row = await memory.set_note(session, ctx.workspace.id, data.merchant_key, data.note)
    await session.commit()
    if row is None:
        # Cleared on a store the memory never saw: nothing was stored.
        return NoteRead(merchant_key=memory.stored_key(data.merchant_key), note=None)
    return NoteRead(merchant_key=row.merchant_key, note=row.note)


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
    result = await review.undo(
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
        _, key, rows = await review.similar(
            session, ctx.workspace.id, data.transaction_id, exclude_rule=data.exclude_rule
        )
    except review.NotFound as exc:
        raise _not_found(exc)
    return SimilarResult(
        merchant_key=key,
        count=len(rows),
        transaction_ids=[row.id for row in rows],
        transactions=[SimilarTransaction(**asdict(row)) for row in rows],
    )


@router.post("/similar/apply", response_model=AppliedResult)
async def apply_similar(
    data: SimilarRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await review.apply_similar(
            session,
            ctx.workspace.id,
            ctx.user_id,
            data.transaction_id,
            exclude_rule=data.exclude_rule,
            transaction_ids=data.transaction_ids,
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


# --- organizer ----------------------------------------------------------------------


def _organizer_error(exc: Exception) -> HTTPException:
    if isinstance(exc, organizer.NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/organizer", response_model=OrganizerRead)
async def get_organizer(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The latest organize run with its proposed ops (each applied split with
    its redistribution run)."""
    view = organizer.view(await organizer.latest_run(session, ctx.workspace.id))
    view["ops"] = await organizer.with_redistributions(session, ctx.workspace.id, view["ops"])
    return view


@router.post("/organizer/run", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def run_organizer(
    response: Response,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Queue an organizer run; one already busy is returned instead."""
    # Restructures the tree and spends the owner's key: an owner/manager call.
    ctx.require_owner()
    run, created = await organizer.request_run(session, ctx.workspace.id)
    await session.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
        return run
    if not categorization_tasks.enqueue_organizer(ctx.workspace.id, run.id):
        await review.mark_enqueue_failed(session, run)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível iniciar o organizador agora; tente de novo em instantes.",
        )
    return run


@router.post("/organizer/ops/{op_id}/apply", response_model=OrganizerApplyResult)
async def apply_organizer_op(
    op_id: str,
    run_id: Optional[uuid.UUID] = Query(None, description="Organize run; the latest by default"),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Apply one proposed op. A split queues the redistribution of its rows."""
    ctx.require_owner()
    try:
        result = await organizer.apply_op(session, ctx.workspace.id, run_id, op_id, ctx.user_id)
    except (organizer.NotFound, organizer.OpConflict) as exc:
        await session.rollback()
        raise _organizer_error(exc)
    await session.commit()
    redistribution = None
    if result["redistribute_run_id"] is not None:
        redistribution = await session.get(CategorizationRun, result["redistribute_run_id"])
        if redistribution is not None and not categorization_tasks.enqueue_organizer(
            ctx.workspace.id, redistribution.id
        ):
            # The op stays applied; the run shows why its rows did not move.
            await review.mark_enqueue_failed(session, redistribution)
            await session.commit()
    return OrganizerApplyResult(
        op=OrganizerOp.model_validate(result["op"]),
        redistribute_run=RunRead.model_validate(redistribution) if redistribution else None,
    )


@router.post("/organizer/ops/{op_id}/retry", response_model=OrganizerApplyResult)
async def retry_organizer_redistribution(
    op_id: str,
    run_id: Optional[uuid.UUID] = Query(None, description="Organize run; the latest by default"),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Queue again the redistribution of an applied split whose run failed or
    stopped at the monthly cap."""
    ctx.require_owner()
    try:
        result = await organizer.retry_redistribution(session, ctx.workspace.id, run_id, op_id)
    except (organizer.NotFound, organizer.OpConflict) as exc:
        await session.rollback()
        raise _organizer_error(exc)
    await session.commit()
    redistribution = await session.get(CategorizationRun, result["redistribute_run_id"])
    if redistribution is not None and not categorization_tasks.enqueue_organizer(
        ctx.workspace.id, redistribution.id
    ):
        await review.mark_enqueue_failed(session, redistribution)
        await session.commit()
    return OrganizerApplyResult(
        op=OrganizerOp.model_validate(result["op"]),
        redistribute_run=RunRead.model_validate(redistribution) if redistribution else None,
    )


@router.post("/organizer/ops/{op_id}/reject", response_model=OrganizerOp)
async def reject_organizer_op(
    op_id: str,
    run_id: Optional[uuid.UUID] = Query(None, description="Organize run; the latest by default"),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_owner()
    try:
        op = await organizer.reject_op(session, ctx.workspace.id, run_id, op_id)
    except (organizer.NotFound, organizer.OpConflict) as exc:
        await session.rollback()
        raise _organizer_error(exc)
    await session.commit()
    return op
