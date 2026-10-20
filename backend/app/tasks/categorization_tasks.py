"""Celery tasks of the categorization pipeline.

`categorize_workspace` runs one pipeline pass for a workspace, after a sync
or import (`enqueue_categorization`) or on demand. It is debounced: while
another `classify` run of the workspace is queued, running or waiting on a
batch, a new trigger does not start a second one. It marks that run instead
(`detail["rerun"]`), and the run re-enqueues an ingest when it finishes, so
no trigger is lost. Only `classify` runs block classification: an organizer
run (`organize` / `redistribute`) touches the category tree, not the rows,
and runs alongside.

`poll_batches` (beat, every 5 minutes) first collects the Message Batches
of runs waiting in `processing_batch` and applies them
(`classifier.collect_batch`), then expires the runs of any kind a dead
worker left busy (`expire_stale_runs`), so a batch that ended while the
beat was down is applied rather than discarded; it calls
`requeue_if_requested` for each run it closes, expired ones included.

`organize_workspace` executes a queued organizer run: `organize` (Opus
proposes a new tree) or `redistribute` (after an approved split, the old
category's rows are re-filed among its children; a large one waits on a
Message Batch that `poll_batches` collects like a classification's).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.app_clock import use_timezone
from app.core.config import get_settings
from app.models.categorization import CategorizationRun
from app.services.categorization import classifier, organizer, pipeline
from app.services.categorization.settings import get_settings as get_categorization_settings
from app.worker import celery_app

logger = logging.getLogger(__name__)

CATEGORIZE_TASK = "app.tasks.categorization_tasks.categorize_workspace"
POLL_BATCHES_TASK = "app.tasks.categorization_tasks.poll_batches"
ORGANIZE_TASK = "app.tasks.categorization_tasks.organize_workspace"
#: A `classify` run in one of these states owns the workspace.
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
#: Run kinds `expire_stale_runs` frees.
RUN_KINDS = ("classify", *organizer.ORGANIZER_KINDS)
#: `detail` flag: a trigger arrived while the run was busy.
RERUN_FLAG = "rerun"
#: First key of the Postgres advisory lock that serializes claims per
#: workspace ("CATG"); the second is taken from the workspace id.
LOCK_NAMESPACE = 0x43415447


def enqueue_categorization(
    workspace_id: uuid.UUID, trigger: str = "ingest", run_id: uuid.UUID | None = None
) -> bool:
    """Ask the worker to categorize a workspace; True when the task was sent.

    Never raises: called right after a sync or import commits, a broker that
    is down must not fail the ingestion (the next trigger catches up). A
    caller that created a `queued` run for `run_id` should mark that run
    failed when this returns False, or it would only be freed as abandoned.
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
        return False
    return True


def enqueue_organizer(workspace_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    """Ask the worker to execute a queued organizer run; True when sent.

    Never raises: the caller marks the run failed when this returns False.
    """
    try:
        celery_app.send_task(ORGANIZE_TASK, args=[str(workspace_id), str(run_id)])
    except Exception:  # noqa: BLE001 - reported to the caller
        logger.warning(
            "could not enqueue the organizer for workspace %s", workspace_id, exc_info=True
        )
        return False
    return True


@celery_app.task(name=CATEGORIZE_TASK)
def categorize_workspace(workspace_id: str, trigger: str, run_id: str | None = None) -> dict:
    """Celery entry point: one pipeline pass, in a fresh event loop."""
    return asyncio.run(_categorize_celery(workspace_id, trigger, run_id))


@celery_app.task(name=POLL_BATCHES_TASK)
def poll_batches() -> dict:
    """Collect the finished Message Batches of runs waiting on one."""
    return asyncio.run(_poll_celery())


@celery_app.task(name=ORGANIZE_TASK)
def organize_workspace(workspace_id: str, run_id: str) -> dict:
    """Celery entry point: one organizer run, in a fresh event loop."""
    return asyncio.run(_organize_celery(workspace_id, run_id))


def _fresh_engine() -> Any:
    # A fresh engine per task: the global pool would hand asyncpg
    # connections bound to loops `asyncio.run` already closed.
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def _categorize_celery(workspace_id: str, trigger: str, run_id: str | None) -> dict:
    engine = _fresh_engine()
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


async def _organize_celery(workspace_id: str, run_id: str) -> dict:
    engine = _fresh_engine()
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await _organize(session_maker, uuid.UUID(workspace_id), uuid.UUID(run_id))
    finally:
        await engine.dispose()


async def _organize(session_maker: Any, workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict:
    async with session_maker() as session:
        async with use_timezone(session, workspace_id, fresh=True):
            return await _organize_in(session, workspace_id, run_id)


async def _organize_in(session: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict:
    await lock_workspace(session, workspace_id)
    claimed = await session.execute(
        update(CategorizationRun)
        .where(
            CategorizationRun.id == run_id,
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.kind.in_(organizer.ORGANIZER_KINDS),
            CategorizationRun.status == "queued",
        )
        .values(status="running")
        .execution_options(synchronize_session=False)
    )
    # Committed before the work: the run shows as busy and the lock is freed.
    await session.commit()
    if getattr(claimed, "rowcount", 0) != 1:
        return {"status": "skipped", "reason": "run not queued"}
    run = await session.get(CategorizationRun, run_id, populate_existing=True)
    if run is None:
        return {"status": "skipped", "reason": "run not queued"}
    try:
        if run.kind == "organize":
            await organizer.organize(session, workspace_id, run)
        else:
            await organizer.redistribute(session, workspace_id, run)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - recorded on the run
        logger.exception("organizer run %s failed", run_id)
        await session.rollback()
        failed = await session.get(CategorizationRun, run_id, populate_existing=True)
        if failed is not None:
            failed.status = "failed"
            failed.error = type(exc).__name__
            failed.finished_at = datetime.now(timezone.utc)
            await session.commit()
        return {"status": "failed", "run_id": str(run_id)}
    return {"status": run.status, "run_id": str(run.id)}


async def _poll_celery() -> dict:
    engine = _fresh_engine()
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await _poll(session_maker)
    finally:
        await engine.dispose()


async def _poll(session_maker: Any) -> dict:
    """One pass over the runs waiting on a batch; each in its own transaction.

    A run whose collection fails (API down, rate limit) stays waiting and is
    retried on the next pass; one that never ends (or any run left busy) is
    expired as abandoned first.
    """
    summary = {"collected": 0, "pending": 0, "failed": 0}
    async with session_maker() as session:
        waiting = (
            await session.execute(
                select(CategorizationRun.id, CategorizationRun.workspace_id)
                .where(
                    CategorizationRun.kind.in_(classifier.BATCH_KINDS),
                    CategorizationRun.status == "processing_batch",
                )
                .order_by(CategorizationRun.created_at, CategorizationRun.id)
            )
        ).all()
    for run_id, workspace_id in waiting:
        async with session_maker() as session:
            async with use_timezone(session, workspace_id, fresh=True):
                try:
                    outcome = await classifier.collect_batch(session, run_id)
                    await session.commit()
                except Exception:  # noqa: BLE001 - retried on the next pass
                    logger.exception("could not collect the batch of run %s", run_id)
                    await session.rollback()
                    summary["failed"] += 1
                    continue
                if outcome in summary:
                    summary[outcome] += 1
                if outcome != "pending":
                    await requeue_if_requested(session, run_id)
    async with session_maker() as session:
        # After the collection: only a batch that still did not end (or
        # could not be read) is given up. Runs a dead worker left busy are
        # freed here too, so a stuck organizer run does not wait for the
        # next request of its workspace.
        expired = await expire_stale_runs(session)
        await session.commit()
        for run_id in expired:
            await requeue_if_requested(session, run_id)
    return summary


async def _categorize(
    session_maker: Any, workspace_id: uuid.UUID, trigger: str, run_id: uuid.UUID | None
) -> dict:
    async with session_maker() as session:
        async with use_timezone(session, workspace_id, fresh=True):
            return await _categorize_in(session, workspace_id, trigger, run_id)


async def lock_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Serialize claims of one workspace until commit (Postgres only).

    Two triggers checking "is a run busy?" at the same time would otherwise
    both see none and start two runs.
    """
    connection = await session.connection()
    if connection.dialect.name != "postgresql":
        return
    await session.execute(select(func.pg_advisory_xact_lock(LOCK_NAMESPACE, _lock_key(workspace_id))))


async def try_lock_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """`lock_workspace` without waiting: False when another transaction
    holds it (True off Postgres, where there is no lock)."""
    connection = await session.connection()
    if connection.dialect.name != "postgresql":
        return True
    return bool(
        await session.scalar(
            select(func.pg_try_advisory_xact_lock(LOCK_NAMESPACE, _lock_key(workspace_id)))
        )
    )


def _lock_key(workspace_id: uuid.UUID) -> int:
    return int.from_bytes(workspace_id.bytes[:4], "big", signed=True)


async def expire_stale_runs(
    session: AsyncSession,
    workspace_id: uuid.UUID | None = None,
    *,
    kinds: tuple[str, ...] = RUN_KINDS,
) -> list[uuid.UUID]:
    """Mark failed (`abandoned`) the runs of `kinds` a dead worker left busy
    past `STALE_AFTER`, of one workspace or (None, from `poll_batches`) of
    all. The one rule for every path that frees a workspace: a trigger, a
    request, the beat.

    Only the beat gives up on a run waiting on its batch, right after it
    tried to collect it: a batch that ended keeps its (paid) results for
    29 days. A path that frees one workspace leaves those runs busy (a
    trigger marks them `rerun` instead).

    Returns the expired ids: `poll_batches` honors their `rerun` marks
    (a trigger or a request expiring a run of its own kind runs anyway).
    A run expired while waiting on its batch keeps the batch's estimate as
    spend: the money was committed when the batch was sent.
    """
    now = datetime.now(timezone.utc)
    scope = [CategorizationRun.workspace_id == workspace_id] if workspace_id is not None else []
    expired: list[uuid.UUID] = []
    for status, age in STALE_AFTER.items():
        if status == "processing_batch" and workspace_id is not None:
            continue
        stale = [
            *scope,
            CategorizationRun.kind.in_(kinds),
            CategorizationRun.status == status,
            CategorizationRun.created_at < now - age,
        ]
        if status == "processing_batch":
            await _charge_batch_estimates(session, stale)
        result = await session.execute(
            update(CategorizationRun)
            .where(*stale)
            .values(status="failed", error=ABANDONED_ERROR, finished_at=now)
            .returning(CategorizationRun.id)
            .execution_options(synchronize_session=False)
        )
        expired.extend(result.scalars().all())
    return expired


async def _charge_batch_estimates(session: AsyncSession, stale: list[Any]) -> None:
    """Add each stale batch run's estimate to its cost before it stops
    counting as in flight (`classifier.committed_spend`)."""
    rows = (
        await session.execute(select(CategorizationRun.id, CategorizationRun.detail).where(*stale))
    ).all()
    for run_id, detail in rows:
        estimate = classifier.batch_estimate(detail)
        if estimate <= 0:
            continue
        await session.execute(
            update(CategorizationRun)
            .where(CategorizationRun.id == run_id, *stale)
            .values(cost_usd=CategorizationRun.cost_usd + estimate)
            .execution_options(synchronize_session=False)
        )


async def _mark_busy_run(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Ask the busy run to re-trigger when it ends; False when none is busy.

    The mark is an UPDATE guarded by the busy status: if the run finished in
    between, nothing is marked and the caller runs itself.
    """
    busy = (
        await session.execute(
            select(CategorizationRun.id, CategorizationRun.detail)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "classify",
                CategorizationRun.status.in_(BUSY_STATUSES),
            )
            .order_by(CategorizationRun.created_at.desc())
            .limit(1)
        )
    ).first()
    if busy is None:
        return False
    detail = {**(busy.detail or {}), RERUN_FLAG: True}
    marked = await session.execute(
        update(CategorizationRun)
        .where(
            CategorizationRun.id == busy.id,
            CategorizationRun.status.in_(BUSY_STATUSES),
        )
        .values(detail=detail)
        .execution_options(synchronize_session=False)
    )
    return getattr(marked, "rowcount", 0) == 1


async def requeue_if_requested(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """After a run ends: enqueue the ingest a busy-time trigger asked for.

    Commits the cleared flag. Returns whether a new trigger was sent. A run
    still busy (e.g. waiting on a batch) is left for `poll_batches`.
    """
    row = (
        await session.execute(
            select(
                CategorizationRun.workspace_id,
                CategorizationRun.status,
                CategorizationRun.detail,
            ).where(CategorizationRun.id == run_id)
        )
    ).first()
    if row is None or row.status in BUSY_STATUSES:
        return False
    detail = dict(row.detail or {})
    if not detail.pop(RERUN_FLAG, False):
        return False
    await session.execute(
        update(CategorizationRun)
        .where(CategorizationRun.id == run_id)
        .values(detail=detail)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return enqueue_categorization(row.workspace_id, "ingest")


async def _claim(
    session: AsyncSession, workspace_id: uuid.UUID, trigger: str, run_id: uuid.UUID | None
) -> CategorizationRun | str:
    """The run this task executes, or why it should not run."""
    await lock_workspace(session, workspace_id)
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
    await expire_stale_runs(session, workspace_id)
    if await _mark_busy_run(session, workspace_id):
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
    # Committed before the work, so the next trigger sees the run as busy
    # (and the advisory lock is released).
    await session.commit()
    if isinstance(claimed, str):
        return {"status": "skipped", "reason": claimed}
    run_pk = claimed.id

    try:
        prepared = await pipeline.prepare_run(
            session, workspace_id, trigger=claimed.trigger, run=claimed, use_ai=True
        )
        # The memory's work is saved before the (slow) classifier, so no row
        # stays locked while the model answers.
        await session.commit()
        # The pipeline falls back to suggestions when no classifier is wired
        # in or the workspace has no usable Claude connection.
        run = await pipeline.complete_run(session, workspace_id, prepared, use_ai=True)
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
        await requeue_if_requested(session, run_pk)
        return {"status": "failed", "run_id": str(run_pk)}
    result = {"status": run.status, "run_id": str(run.id), "stats": dict(run.stats or {})}
    await requeue_if_requested(session, run_pk)
    return result
