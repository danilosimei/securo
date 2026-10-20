"""Celery tasks of the categorization pipeline.

`categorize_workspace` runs one pipeline pass for a workspace, after a sync
or import (`enqueue_categorization`) or on demand. It is debounced: while
another run of the workspace is queued, running or waiting on a batch, a new
trigger exits, since that run will see the new rows or the next one will.
`poll_batches` collects finished Message Batches (filled in with the Claude
classifier).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.app_clock import use_timezone
from app.core.config import get_settings
from app.models.categorization import CategorizationRun
from app.services.categorization import pipeline
from app.services.categorization.settings import get_settings as get_categorization_settings
from app.worker import celery_app

logger = logging.getLogger(__name__)

CATEGORIZE_TASK = "app.tasks.categorization_tasks.categorize_workspace"
POLL_BATCHES_TASK = "app.tasks.categorization_tasks.poll_batches"
#: A run in one of these states owns the workspace: new triggers exit.
BUSY_STATUSES = ("queued", "running", "processing_batch")
#: Busy runs older than this were abandoned (a worker died mid-run); they
#: are marked failed so they stop blocking the workspace. A Message Batch
#: may take up to 24 h, hence the longer wait for those.
STALE_AFTER = {
    "queued": timedelta(hours=6),
    "running": timedelta(hours=6),
    "processing_batch": timedelta(hours=30),
}
ABANDONED_ERROR = "abandoned"


def enqueue_categorization(
    workspace_id: uuid.UUID, trigger: str = "ingest", run_id: uuid.UUID | None = None
) -> None:
    """Ask the worker to categorize a workspace. Never raises.

    Called right after a sync or import commits: a broker that is down must
    not fail the ingestion, and the next trigger will catch up.
    """
    try:
        celery_app.send_task(
            CATEGORIZE_TASK,
            args=[str(workspace_id), trigger, str(run_id) if run_id is not None else None],
        )
    except Exception:  # noqa: BLE001 - categorization is best effort
        logger.warning(
            "could not enqueue categorization for workspace %s", workspace_id, exc_info=True
        )


@celery_app.task(name=CATEGORIZE_TASK)
def categorize_workspace(workspace_id: str, trigger: str, run_id: str | None = None) -> dict:
    """Celery entry point: one pipeline pass, in a fresh event loop."""
    return asyncio.run(_categorize_celery(workspace_id, trigger, run_id))


@celery_app.task(name=POLL_BATCHES_TASK)
def poll_batches() -> None:
    """Collect finished Message Batches; nothing is sent in batches yet."""
    return None


async def _categorize_celery(workspace_id: str, trigger: str, run_id: str | None) -> dict:
    # A fresh engine per task: the global pool would hand asyncpg
    # connections bound to loops `asyncio.run` already closed.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await _categorize(
            session_maker,
            uuid.UUID(workspace_id),
            trigger,
            uuid.UUID(run_id) if run_id else None,
        )
    finally:
        await engine.dispose()


async def _categorize(
    session_maker: Any, workspace_id: uuid.UUID, trigger: str, run_id: uuid.UUID | None
) -> dict:
    async with session_maker() as session:
        async with use_timezone(session, workspace_id, fresh=True):
            return await _categorize_in(session, workspace_id, trigger, run_id)


async def _expire_stale_runs(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    for status, age in STALE_AFTER.items():
        await session.execute(
            update(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.status == status,
                CategorizationRun.created_at < now - age,
            )
            .values(status="failed", error=ABANDONED_ERROR, finished_at=now)
            .execution_options(synchronize_session=False)
        )


async def _claim(
    session: AsyncSession, workspace_id: uuid.UUID, trigger: str, run_id: uuid.UUID | None
) -> CategorizationRun | str:
    """The run this task executes, or why it should not run."""
    if run_id is not None:
        # A run queued by a person (the API creates it, then enqueues): take
        # it unless another worker already did.
        claimed = await session.execute(
            update(CategorizationRun)
            .where(
                CategorizationRun.id == run_id,
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.status == "queued",
            )
            .values(status="running")
            .execution_options(synchronize_session=False)
        )
        if getattr(claimed, "rowcount", 0) != 1:
            return "run not queued"
        run = await session.get(CategorizationRun, run_id, populate_existing=True)
        return run if run is not None else "run not queued"

    settings = await get_categorization_settings(session, workspace_id)
    if not settings.enabled and trigger != "manual":
        return "disabled"
    await _expire_stale_runs(session, workspace_id)
    busy = await session.scalar(
        select(CategorizationRun.id)
        .where(
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.status.in_(BUSY_STATUSES),
        )
        .limit(1)
    )
    if busy is not None:
        return "busy"
    run = CategorizationRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind="classify",
        trigger=trigger,
        status="running",
        stats={},
        detail={},
    )
    session.add(run)
    return run


async def _categorize_in(
    session: AsyncSession, workspace_id: uuid.UUID, trigger: str, run_id: uuid.UUID | None
) -> dict:
    claimed = await _claim(session, workspace_id, trigger, run_id)
    # Committed before the work, so the next trigger sees the run as busy.
    await session.commit()
    if isinstance(claimed, str):
        return {"status": "skipped", "reason": claimed}
    run_pk = claimed.id

    try:
        # The pipeline falls back to suggestions when no classifier is wired
        # in or the workspace has no usable Claude connection.
        run = await pipeline.run_categorization(
            session, workspace_id, trigger=claimed.trigger, use_ai=True, run=claimed
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - recorded on the run
        logger.exception("categorization run %s failed", run_pk)
        await session.rollback()
        failed = await session.get(CategorizationRun, run_pk, populate_existing=True)
        if failed is not None:
            failed.status = "failed"
            failed.error = type(exc).__name__
            failed.finished_at = datetime.now(timezone.utc)
            await session.commit()
        return {"status": "failed", "run_id": str(run_pk)}
    return {"status": run.status, "run_id": str(run.id), "stats": dict(run.stats or {})}
