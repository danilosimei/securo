import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from kombu.exceptions import OperationalError
from sqlalchemy import delete, select

from app.models.categorization import CategorizationRun
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services.import_service import import_transactions
from app.tasks import categorization_tasks
from app.tasks.categorization_tasks import (
    CATEGORIZE_TASK,
    _categorize,
    enqueue_categorization,
)
from app.worker import celery_app


def _session_maker(session):
    def make():
        return session

    return make


async def _reload(session, run_id):
    return await session.get(CategorizationRun, run_id, populate_existing=True)


def _run(workspace_id, status, *, age=timedelta(0), trigger="ingest", kind="classify"):
    return CategorizationRun(
        id=uuid.uuid4(), workspace_id=workspace_id, kind=kind, trigger=trigger,
        status=status, created_at=datetime.now(timezone.utc) - age, detail={},
    )


def test_enqueue_never_raises_when_broker_down():
    ws = uuid.uuid4()
    with patch.object(celery_app, "send_task", side_effect=OperationalError("down")) as send:
        assert enqueue_categorization(ws) is False
    send.assert_called_once()
    with patch.object(celery_app, "send_task", side_effect=RuntimeError("boom")):
        assert enqueue_categorization(ws, "manual") is False


def test_enqueue_sends_task_with_workspace_and_trigger():
    ws = uuid.uuid4()
    run_id = uuid.uuid4()
    with patch.object(celery_app, "send_task") as send:
        assert enqueue_categorization(ws, "backlog") is True
        assert enqueue_categorization(ws, "manual", run_id) is True
    assert send.call_args_list[0].args == (CATEGORIZE_TASK,)
    assert send.call_args_list[0].kwargs == {"args": [str(ws), "backlog", None]}
    assert send.call_args_list[1].kwargs == {"args": [str(ws), "manual", str(run_id)]}


def test_tasks_are_registered_and_scheduled():
    assert CATEGORIZE_TASK == "app.tasks.categorization_tasks.categorize_workspace"
    assert "app.tasks.categorization_tasks" in celery_app.conf.include
    entry = celery_app.conf.beat_schedule["categorization-poll-batches"]
    assert entry["task"] == "app.tasks.categorization_tasks.poll_batches"
    assert entry["schedule"] == 5 * 60
    assert categorization_tasks.poll_batches.name == entry["task"]
    assert categorization_tasks.categorize_workspace.name == CATEGORIZE_TASK
    summary = {"collected": 0, "pending": 0, "failed": 0}
    with patch.object(categorization_tasks, "_poll", AsyncMock(return_value=summary)) as poll:
        assert categorization_tasks.poll_batches() == summary
    poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_categorize_task_debounces(session, test_workspace):
    busy = _run(test_workspace.id, "running")
    session.add(busy)
    await session.commit()

    with patch.object(
        categorization_tasks.pipeline, "prepare_run", new_callable=AsyncMock
    ) as run:
        result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)

    run.assert_not_awaited()
    assert result == {"status": "skipped", "reason": "busy"}
    assert (await _reload(session, busy.id)).detail == {"rerun": True}


@pytest.mark.asyncio
async def test_organizer_run_does_not_block_classification(session, test_workspace):
    session.add(_run(test_workspace.id, "running", kind="organize"))
    await session.commit()

    result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)

    assert result["status"] == "done"


@pytest.mark.asyncio
async def test_finished_run_requeues_trigger_received_while_busy(
    session, test_workspace, categorization_enqueue
):
    queued = _run(test_workspace.id, "queued", trigger="manual")
    session.add(queued)
    await session.commit()
    # A sync lands while the manual run waits: it marks the run, does not start.
    skipped = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)
    assert skipped == {"status": "skipped", "reason": "busy"}
    categorization_enqueue.assert_not_called()

    result = await _categorize(_session_maker(session), test_workspace.id, "manual", queued.id)

    assert result["status"] == "done"
    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")
    assert (await _reload(session, queued.id)).detail == {}


@pytest.mark.asyncio
async def test_failed_run_also_requeues(session, test_workspace, categorization_enqueue):
    async def fail_after_mark(session_, workspace_id, *, trigger, run, use_ai=False):
        # Another trigger arrives mid-run, then the run blows up.
        assert await categorization_tasks._mark_busy_run(session_, workspace_id)
        await session_.commit()
        raise RuntimeError("boom")

    with patch.object(categorization_tasks.pipeline, "prepare_run", side_effect=fail_after_mark):
        result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)

    assert result["status"] == "failed"
    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")


@pytest.mark.asyncio
async def test_requeue_if_requested_leaves_busy_and_unmarked_runs(session, test_workspace):
    from app.tasks.categorization_tasks import requeue_if_requested

    batch = _run(test_workspace.id, "processing_batch")
    batch.detail = {"rerun": True}
    done = _run(test_workspace.id, "done")
    session.add_all([batch, done])
    await session.commit()

    assert await requeue_if_requested(session, batch.id) is False
    assert await requeue_if_requested(session, done.id) is False
    assert await requeue_if_requested(session, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_categorize_task_debounces_on_queued_and_batch(session, test_workspace):
    for status in ("queued", "processing_batch"):
        busy = _run(test_workspace.id, status)
        session.add(busy)
        await session.commit()
        with patch.object(
            categorization_tasks.pipeline, "prepare_run", new_callable=AsyncMock
        ) as run:
            await _categorize(_session_maker(session), test_workspace.id, "ingest", None)
        run.assert_not_awaited()
        await session.execute(delete(CategorizationRun).where(CategorizationRun.id == busy.id))
        await session.commit()


@pytest.mark.asyncio
async def test_categorize_task_ignores_stale_run_and_marks_it_failed(
    session, test_user, test_workspace, test_account
):
    stale = _run(test_workspace.id, "running", age=timedelta(hours=7))
    session.add(stale)
    await session.commit()

    result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)

    assert result["status"] == "done"
    stale = await _reload(session, stale.id)
    assert stale.status == "failed"
    assert stale.error == "abandoned"


@pytest.mark.asyncio
async def test_categorize_task_runs_and_commits(session, test_user, test_workspace, test_account):
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=test_account.id, description="IFOOD *REST 8812",
            amount=Decimal("30"), date=date(2026, 9, 1), effective_date=date(2026, 9, 1),
            type="debit", source="sync", status="posted",
        )
    )
    await session.commit()

    result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)

    assert result["status"] == "done"
    [run] = (await session.execute(select(CategorizationRun))).scalars().all()
    assert run.status == "done"
    assert run.stats["suggested"] == 1


@pytest.mark.asyncio
async def test_categorize_task_claims_queued_run(session, test_workspace):
    queued = _run(test_workspace.id, "queued", trigger="manual")
    session.add(queued)
    await session.commit()

    result = await _categorize(_session_maker(session), test_workspace.id, "manual", queued.id)

    assert result["status"] == "done"
    assert (await _reload(session, queued.id)).status == "done"
    runs = (await session.execute(select(CategorizationRun))).scalars().all()
    assert len(runs) == 1

    again = await _categorize(_session_maker(session), test_workspace.id, "manual", queued.id)
    assert again == {"status": "skipped", "reason": "run not queued"}


@pytest.mark.asyncio
async def test_categorize_task_skips_disabled_workspace_unless_manual(session, test_workspace):
    from app.services.categorization.settings import get_settings

    settings = await get_settings(session, test_workspace.id)
    settings.enabled = False
    await session.commit()

    result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)
    assert result == {"status": "skipped", "reason": "disabled"}

    result = await _categorize(_session_maker(session), test_workspace.id, "manual", None)
    assert result["status"] == "done"


@pytest.mark.asyncio
async def test_categorize_task_marks_run_failed_on_error(session, test_workspace):
    with patch.object(
        categorization_tasks.pipeline, "prepare_run",
        new_callable=AsyncMock, side_effect=RuntimeError("boom"),
    ):
        result = await _categorize(_session_maker(session), test_workspace.id, "ingest", None)

    assert result["status"] == "failed"
    [run] = (await session.execute(select(CategorizationRun))).scalars().all()
    assert run.status == "failed"
    assert run.error == "RuntimeError"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_import_enqueues_categorization(
    session, test_user, test_workspace, test_account, categorization_enqueue
):
    await import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [TransactionImport(
            description="MERCADO X", amount=Decimal("30"), date=date(2026, 1, 5), type="debit",
        )],
        "csv",
    )
    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")


@pytest.mark.asyncio
async def test_import_without_rows_does_not_enqueue(
    session, test_user, test_workspace, test_account, categorization_enqueue
):
    await import_transactions(session, test_workspace.id, test_user.id, test_account.id, [], "csv")
    categorization_enqueue.assert_not_called()


def _provider(transactions):
    from app.providers.base import AccountData, ConnectionData

    account = AccountData(
        external_id="hook-acc", name="Checking", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[account])
    provider.get_transactions = AsyncMock(return_value=transactions)
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="hook-conn", institution_name="Banco", credentials={"token": "abc"},
        accounts=[account],
    ))
    return provider


def _provider_tx(external_id, status="posted"):
    from app.providers.base import TransactionData

    return TransactionData(
        external_id=external_id, description="RESTAURANTE", amount=Decimal("40"),
        date=date.today(), type="debit", currency="BRL", status=status,
    )


def _provider_patches(provider):
    return (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_oauth_callback_enqueues_categorization(
    session, test_user, test_workspace, categorization_enqueue
):
    from app.services.connection_service import handle_oauth_callback

    a, b, c = _provider_patches(_provider([_provider_tx("hook-oauth-tx")]))
    with a, b, c:
        await handle_oauth_callback(session, test_workspace.id, test_user.id, "code", "pluggy")

    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")


@pytest.mark.asyncio
async def test_sync_enqueues_categorization_only_when_rows_land(
    session, test_user, test_workspace, categorization_enqueue
):
    from app.models.bank_connection import BankConnection
    from app.services.connection_service import sync_connection

    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, provider="test",
        external_id="hook-sync", institution_name="Banco", credentials={"token": "fake"},
        status="active",
    )
    session.add(conn)
    await session.commit()

    a, b, c = _provider_patches(_provider([]))
    with a, b, c:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    categorization_enqueue.assert_not_called()

    a, b, c = _provider_patches(_provider([_provider_tx("hook-sync-tx")]))
    with a, b, c:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")


@pytest.mark.asyncio
async def test_sync_enqueues_when_pending_row_is_posted(
    session, test_user, test_workspace, categorization_enqueue
):
    from app.models.bank_connection import BankConnection
    from app.services.connection_service import sync_connection

    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, provider="test",
        external_id="hook-promote", institution_name="Banco", credentials={"token": "fake"},
        status="active",
    )
    session.add(conn)
    await session.commit()
    a, b, c = _provider_patches(_provider([_provider_tx("promo-tx", "pending")]))
    with a, b, c:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    categorization_enqueue.reset_mock()

    a, b, c = _provider_patches(_provider([_provider_tx("promo-tx", "posted")]))
    with a, b, c:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    tx = (await session.execute(
        select(Transaction).where(Transaction.external_id == "promo-tx")
        .execution_options(populate_existing=True)
    )).scalar_one()
    assert tx.status == "posted"
    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")


@pytest.mark.asyncio
async def test_poll_expires_stale_runs_of_every_kind(session, test_workspace):
    """The periodic pass frees what a dead worker left busy, organizer runs
    included: a stuck redistribution must not wait for the next request."""
    from app.tasks.categorization_tasks import _poll

    stale = [
        _run(test_workspace.id, "running", age=timedelta(hours=7), kind="redistribute"),
        _run(test_workspace.id, "processing_batch", age=timedelta(hours=31), kind="redistribute"),
        _run(test_workspace.id, "queued", age=timedelta(hours=7), kind="organize"),
        _run(test_workspace.id, "processing_batch", age=timedelta(hours=31), kind="classify"),
    ]
    fresh = _run(test_workspace.id, "running", age=timedelta(hours=1), kind="redistribute")
    session.add_all([*stale, fresh])
    await session.commit()

    await _poll(_session_maker(session))

    for run in stale:
        run = await _reload(session, run.id)
        assert (run.status, run.error) == ("failed", "abandoned"), run.kind
        assert run.finished_at is not None
    assert (await _reload(session, fresh.id)).status == "running"


@pytest.mark.asyncio
async def test_expired_batch_keeps_its_estimate_as_spend(session, test_workspace):
    """The money was committed when the batch was sent: expiring the run
    must not take it out of the month's spend."""
    from app.tasks.categorization_tasks import expire_stale_runs

    ws = test_workspace.id
    batch = _run(ws, "processing_batch", age=timedelta(hours=31))
    batch.cost_usd = Decimal("0.01")
    batch.detail = {"batch": {"estimated_cost_usd": "0.25"}, "rerun": True}
    no_estimate = _run(ws, "processing_batch", age=timedelta(hours=31), kind="redistribute")
    running = _run(ws, "running", age=timedelta(hours=7))
    running.detail = {"batch": {"estimated_cost_usd": "9"}}
    session.add_all([batch, no_estimate, running])
    await session.commit()

    expired = await expire_stale_runs(session, ws)
    await session.commit()

    assert set(expired) == {batch.id, no_estimate.id, running.id}
    assert (await _reload(session, batch.id)).cost_usd == Decimal("0.26")
    assert (await _reload(session, no_estimate.id)).cost_usd == Decimal("0")
    assert (await _reload(session, running.id)).cost_usd == Decimal("0")


@pytest.mark.asyncio
async def test_poll_requeues_the_trigger_an_expired_run_carried(
    session, test_workspace, categorization_enqueue
):
    from app.tasks.categorization_tasks import _poll

    stale = _run(test_workspace.id, "processing_batch", age=timedelta(hours=31))
    stale.detail = {"rerun": True}
    session.add(stale)
    await session.commit()

    await _poll(_session_maker(session))

    categorization_enqueue.assert_called_once_with(test_workspace.id, "ingest")
    assert (await _reload(session, stale.id)).detail == {}
