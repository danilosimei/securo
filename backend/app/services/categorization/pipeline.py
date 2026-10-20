"""The categorization pipeline: candidates -> stores -> memory -> model or person.

After every sync or import (and on demand) the transactions whose category
is still a guess are grouped by merchant key. The workspace's memory settles
what it can for free: a store the person categorized consistently, or a
decision the model already made with enough confidence. What is left goes
to the classifier when one is available (`ai_classifier`, wired in by the
Claude integration) or becomes a pending suggestion for the person.

Nothing here commits: the caller (the Celery task) owns the transaction.
"""
from __future__ import annotations

import uuid
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.categorization import CategorizationRun, CategorizationSuggestion
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services.categorization.events import REVIEWABLE_SOURCES, stage_change, write_staged
from app.services.categorization.memory import MemoryHit, lookup
from app.services.categorization.merchant_key import merchant_key, sample_description
from app.services.categorization.settings import get_settings

#: Example descriptions per store shown to the model and to the person.
MAX_SAMPLES = 2
#: Recurring = this many distinct months with an amount within the tolerance.
RECURRING_MONTHS = 3
RECURRING_TOLERANCE = Decimal("0.05")
#: Reason noted on the events that reuse a cached model decision.
CACHED_AI_REASON = "decisão anterior da IA"
#: Origins the pipeline itself wrote: a row already settled by it is not
#: sent to the model or to the person again unless its memory changes.
PIPELINE_SOURCES = frozenset({"memory", "ai"})
#: `merchant_key` column width on suggestions.
KEY_WIDTH = 255
#: Ids per `IN (...)` (asyncpg caps a statement at 32767 parameters).
CHUNK = 1000


@dataclass(frozen=True)
class Candidate:
    tx_id: uuid.UUID
    key: str
    description: str
    payee_name: str | None
    amount: Decimal
    type: str
    account_type: str | None
    date: date
    current_category_id: uuid.UUID | None
    current_source: str = "none"


@dataclass
class MerchantGroup:
    key: str
    tx_ids: list[uuid.UUID]
    samples: list[str]
    count: int
    median_amount: Decimal
    type: str
    recurring: bool
    account_type: str | None
    candidates: list[Candidate] = field(default_factory=list, repr=False)


AiClassifier = Callable[
    [AsyncSession, uuid.UUID, dict[str, MerchantGroup], CategorizationRun], Awaitable[None]
]
#: Seam for the Claude classifier: when set and the run may use AI, it gets
#: the groups the memory could not settle (and owns their suggestions and
#: the run's AI counters). None means "no classifier": suggestions only.
ai_classifier: AiClassifier | None = None


def _chunks(items: list[uuid.UUID], size: int = CHUNK) -> Iterator[list[uuid.UUID]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def stored_key(key: str) -> str:
    return key[:KEY_WIDTH]


async def _dismissed_at(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, datetime]:
    """Store key -> when the person last dismissed its suggestions."""
    when = func.max(
        func.coalesce(CategorizationSuggestion.resolved_at, CategorizationSuggestion.created_at)
    )
    rows = await session.execute(
        select(CategorizationSuggestion.merchant_key, when)
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.status == "dismissed",
        )
        .group_by(CategorizationSuggestion.merchant_key)
    )
    return {key: _utc(at) for key, at in rows.all() if at is not None}


async def collect_candidates(session: AsyncSession, workspace_id: uuid.UUID) -> list[Candidate]:
    """Posted, unpaired, counted transactions whose category is still a guess.

    Left out: rows with a pending suggestion (already waiting for the
    person), and rows of a store whose suggestions the person dismissed
    after the row arrived -- "ignore" holds until a new purchase of that
    store lands.
    """
    pending = exists().where(
        CategorizationSuggestion.transaction_id == Transaction.id,
        CategorizationSuggestion.status == "pending",
    )
    rows = await session.execute(
        select(
            Transaction.id,
            Transaction.description,
            Transaction.amount,
            Transaction.type,
            Transaction.date,
            Transaction.category_id,
            Transaction.category_source,
            Transaction.created_at,
            Account.type.label("account_type"),
            Payee.name.label("payee_name"),
            Payee.source.label("payee_source"),
        )
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(
            Payee,
            and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.status == "posted",
            Transaction.transfer_pair_id.is_(None),
            Transaction.source != "opening_balance",
            Transaction.is_ignored.is_(False),
            Transaction.category_source.in_(REVIEWABLE_SOURCES),
            ~pending,
        )
        .order_by(Transaction.date, Transaction.id)
    )
    dismissed = await _dismissed_at(session, workspace_id)
    candidates: list[Candidate] = []
    for row in rows.all():
        key = merchant_key(row.description, payee_name=row.payee_name, payee_source=row.payee_source)
        dismissed_at = dismissed.get(stored_key(key))
        if dismissed_at is not None and (
            row.created_at is None or _utc(row.created_at) <= dismissed_at
        ):
            continue
        candidates.append(
            Candidate(
                tx_id=row.id,
                key=key,
                description=row.description,
                payee_name=row.payee_name,
                amount=row.amount,
                type=row.type,
                account_type=row.account_type,
                date=row.date,
                current_category_id=row.category_id,
                current_source=row.category_source,
            )
        )
    return candidates


def _most_common(values: Iterable[Any]) -> Any:
    """The most frequent value; ties go to the first seen."""
    return Counter(values).most_common(1)[0][0]


def _samples(cands: list[Candidate]) -> list[str]:
    counts = Counter(sample_description(c.description) for c in cands)
    return [text for text, _ in counts.most_common() if text][:MAX_SAMPLES]


def _is_recurring(cands: list[Candidate]) -> bool:
    """At least `RECURRING_MONTHS` distinct months with a similar amount.

    Similar = within `RECURRING_TOLERANCE` of some amount of the store, so a
    subscription is found even among the store's one-off purchases.
    """
    if len({(c.date.year, c.date.month) for c in cands}) < RECURRING_MONTHS:
        return False
    points = sorted((abs(c.amount), (c.date.year, c.date.month)) for c in cands)
    amounts = [amount for amount, _ in points]
    for reference in sorted(set(amounts)):
        if reference <= 0:
            continue
        low = bisect_left(amounts, reference * (1 - RECURRING_TOLERANCE))
        high = bisect_right(amounts, reference * (1 + RECURRING_TOLERANCE))
        if high - low >= RECURRING_MONTHS and len(
            {month for _, month in points[low:high]}
        ) >= RECURRING_MONTHS:
            return True
    return False


def group_by_key(cands: Iterable[Candidate]) -> dict[str, MerchantGroup]:
    """One group per store, in order of first appearance."""
    by_key: dict[str, list[Candidate]] = {}
    for cand in cands:
        by_key.setdefault(cand.key, []).append(cand)
    return {
        key: MerchantGroup(
            key=key,
            tx_ids=[c.tx_id for c in members],
            samples=_samples(members),
            count=len(members),
            median_amount=Decimal(median(abs(c.amount) for c in members)),
            type=_most_common(c.type for c in members),
            recurring=_is_recurring(members),
            account_type=_most_common(c.account_type for c in members),
            candidates=members,
        )
        for key, members in by_key.items()
    }


@dataclass(frozen=True)
class _Decision:
    category_id: uuid.UUID
    source: str
    confidence: int | None = None
    reason: str | None = None


def _decide(hit: MemoryHit | None, threshold: int) -> _Decision | None:
    """What the memory settles for a store: the person's habit first."""
    if hit is None:
        return None
    if hit.consistent and hit.category_id is not None:
        return _Decision(hit.category_id, "memory")
    if (
        hit.ai_category_id is not None
        and hit.ai_confidence is not None
        and hit.ai_confidence >= threshold
    ):
        return _Decision(hit.ai_category_id, "ai", hit.ai_confidence, CACHED_AI_REASON)
    return None


async def memory_pass(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    groups: dict[str, MerchantGroup],
    run_id: uuid.UUID | None,
    *,
    stats: dict[str, Any] | None = None,
) -> dict[str, MerchantGroup]:
    """Apply what the memory settles; return the groups it could not.

    A consistent memory applies as `memory`; a cached model decision at or
    above the workspace threshold applies as `ai`. A row already at the
    settled category is left alone (a provider guess that agrees keeps its
    origin). `stats["memory"]` / `stats["applied"]` count the rows changed.
    """
    if not groups:
        return {}
    settings = await get_settings(session, workspace_id)
    hits = await lookup(session, workspace_id, list(groups))

    plan: dict[uuid.UUID, _Decision] = {}
    remaining: dict[str, MerchantGroup] = {}
    for key, group in groups.items():
        decision = _decide(hits.get(key), settings.auto_apply_threshold)
        if decision is None:
            remaining[key] = group
            continue
        for cand in group.candidates:
            if cand.current_category_id != decision.category_id:
                plan[cand.tx_id] = decision

    staged = []
    for ids in _chunks(list(plan)):
        txs = await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id, Transaction.id.in_(ids)
            )
        )
        for tx in txs.scalars().all():
            # The row may have moved on since it was collected.
            if tx.category_source not in REVIEWABLE_SOURCES:
                continue
            decision = plan[tx.id]
            change = stage_change(
                tx,
                source=decision.source,
                new_category_id=decision.category_id,
                run_id=run_id,
                reason=decision.reason,
                confidence=decision.confidence,
            )
            if change is not None:
                staged.append(change)
    await write_staged(session, staged)

    if stats is not None:
        for change in staged:
            counter = "memory" if change.source == "memory" else "applied"
            stats[counter] = stats.get(counter, 0) + 1
    return remaining


async def suggest_without_ai(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    groups: dict[str, MerchantGroup],
    run_id: uuid.UUID | None,
) -> int:
    """Queue the uncategorized rows of `groups` for the person; returns how many.

    The suggestion carries no category (there is no model to propose one).
    Rows that already hold a category keep it: without a model there is
    nothing better to ask about. At most one pending suggestion per row.
    """
    wanted = [
        (cand.tx_id, group.key)
        for group in groups.values()
        for cand in group.candidates
        if cand.current_category_id is None
    ]
    if not wanted:
        return 0
    already: set[uuid.UUID] = set()
    for ids in _chunks([tx_id for tx_id, _ in wanted]):
        already.update(
            (
                await session.execute(
                    select(CategorizationSuggestion.transaction_id).where(
                        CategorizationSuggestion.workspace_id == workspace_id,
                        CategorizationSuggestion.status == "pending",
                        CategorizationSuggestion.transaction_id.in_(ids),
                    )
                )
            ).scalars().all()
        )
    now = datetime.now(timezone.utc)
    payload = [
        {
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "transaction_id": tx_id,
            "run_id": run_id,
            "merchant_key": stored_key(key),
            "status": "pending",
            "created_at": now,
        }
        for tx_id, key in wanted
        if tx_id not in already
    ]
    if payload:
        await session.execute(sa_insert(CategorizationSuggestion), payload)
    return len(payload)


def _undecided(groups: dict[str, MerchantGroup]) -> dict[str, MerchantGroup]:
    """Drop rows the pipeline itself already settled (and still stand)."""
    return group_by_key(
        cand
        for group in groups.values()
        for cand in group.candidates
        if not (cand.current_source in PIPELINE_SOURCES and cand.current_category_id is not None)
    )


async def run_categorization(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    trigger: str,
    use_ai: bool,
    run: CategorizationRun | None = None,
) -> CategorizationRun:
    """One pass of the pipeline, recorded on a `classify` run.

    Uses `run` when the caller already created it (a queued manual run),
    otherwise creates one. Without a classifier (or with `use_ai` off) the
    stores the memory could not settle become pending suggestions.
    """
    settings = await get_settings(session, workspace_id)
    if run is None:
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
    else:
        run.status = "running"
    await session.flush()

    candidates = await collect_candidates(session, workspace_id)
    groups = group_by_key(candidates)
    stats: dict[str, Any] = {
        "candidates": len(candidates),
        "groups": len(groups),
        "memory": 0,
        "applied": 0,
        "suggested": 0,
        "skipped": 0,
    }
    remaining = _undecided(await memory_pass(session, workspace_id, groups, run.id, stats=stats))

    classifier = ai_classifier
    if use_ai and classifier is not None:
        run.stats = stats
        await classifier(session, workspace_id, remaining, run)
    else:
        stats["suggested"] = await suggest_without_ai(session, workspace_id, remaining, run.id)
        stats["skipped"] = (
            stats["candidates"] - stats["memory"] - stats["applied"] - stats["suggested"]
        )
        run.stats = stats

    now = datetime.now(timezone.utc)
    if run.status == "running":
        run.status = "done"
    if run.status in ("done", "capped"):
        run.finished_at = now
    settings.last_run_at = now
    await session.flush()
    return run
