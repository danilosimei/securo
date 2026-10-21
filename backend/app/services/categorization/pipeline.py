"""The categorization pipeline: candidates -> stores -> memory -> model or person.

After every sync or import (and on demand) the transactions whose category
is still a guess are grouped by merchant key. The workspace's memory settles
what it can for free: a store the person categorized consistently, or a
decision the model already made with enough confidence. What is left goes
to the classifier when one is available (`ai_classifier`, which
`classifier` sets when imported -- the Celery task module imports it) or
becomes a pending suggestion for the person.

A pass has two phases so the caller can commit between them:
`prepare_run` (candidates, memory) and `complete_run` (classifier or
suggestions); `run_categorization` chains both. The phases themselves
never commit: the caller (the Celery task) commits after `prepare_run`, so
no row stays locked while the model answers, and again after
`complete_run`. The one exception is inside the classifier seam:
`classifier.classify` commits right after each paid Messages call, so its
cost reaches the monthly cap even if applying the answer then fails and the
task rolls back -- which also commits whatever `complete_run` had pending
at that point (nothing but the run row, by construction).
"""
from __future__ import annotations

import uuid
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.categorization import CategorizationRun, CategorizationSuggestion
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services.categorization.events import (
    REVIEWABLE_SOURCES,
    StagedChange,
    cleared_by_person,
    stage_change,
    write_staged,
)
from app.services.categorization.memory import CHUNK, MemoryHit, lookup, stored_key, teaches
from app.services.categorization.merchant_key import merchant_key, sample_description
from app.services.categorization.settings import find_ai_connection, get_settings
from app.services.category_service import get_assignable_category_ids

#: Example descriptions per store shown to the model and to the person.
MAX_SAMPLES = 2
#: Recurring = this many distinct months with an amount within the tolerance.
RECURRING_MONTHS = 3
RECURRING_TOLERANCE = Decimal("0.05")
#: Reason noted on the events that reuse a cached model decision.
CACHED_AI_REASON = "decisão anterior da IA"
#: Origins the pipeline itself wrote: a row already settled by it is not
#: sent to the model or to the person again.
PIPELINE_SOURCES = frozenset({"memory", "ai"})
#: Origins the memory (and the cached model decision) may overwrite on its
#: own. A row the pipeline already settled keeps its category even when the
#: memory later points elsewhere: older rows change only when the person
#: accepts "apply to the N previous" (spec section 5).
AUTO_APPLY_SOURCES = frozenset({"none", "provider"})
#: `reason` of the suggestions made with no model behind them, so a later
#: run with a model can tell them from the model's own and revisit them.
NO_AI_REASON = "sem IA"
#: `reason` given to a `NO_AI_REASON` suggestion when a later decision (the
#: memory or the model) replaced it. It is closed as `dismissed`, but it is
#: not the person ignoring the store.
SUPERSEDED_REASON = "substituída"
#: Run statuses whose `created_at` bounds what the next ingest looks at.
FINISHED_STATUSES = ("done", "capped")
#: An ingest run also revisits rows this much older than the previous run:
#: a card purchase lands as `pending` and is promoted to `posted` days later
#: keeping its `created_at`, and only posted rows are candidates.
INGEST_LOOKBACK = timedelta(days=30)


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
    #: The person cleared this row's category (R27): only suggested, never applied.
    cleared: bool = False


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
            or_(
                CategorizationSuggestion.reason.is_(None),
                CategorizationSuggestion.reason != SUPERSEDED_REASON,
            ),
        )
        .group_by(CategorizationSuggestion.merchant_key)
    )
    return {key: _utc(at) for key, at in rows.all() if at is not None}


async def ingest_since(session: AsyncSession, workspace_id: uuid.UUID) -> datetime | None:
    """Where an ingest run starts looking: the start of the previous finished
    run, minus `INGEST_LOOKBACK`. None (the whole history) before the first."""
    last = await session.scalar(
        select(func.max(CategorizationRun.created_at)).where(
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.kind == "classify",
            CategorizationRun.status.in_(FINISHED_STATUSES),
        )
    )
    return _utc(last) - INGEST_LOOKBACK if last is not None else None


def model_less_suggestion() -> Any:
    """A suggestion made with no model behind it."""
    return and_(
        CategorizationSuggestion.reason == NO_AI_REASON,
        CategorizationSuggestion.category_id.is_(None),
    )


def blocking_suggestion() -> Any:
    """Any other suggestion: the negation of `model_less_suggestion`, NULL-safe
    (a plain NOT is NULL, not true, when `reason` is NULL)."""
    return or_(
        CategorizationSuggestion.reason.is_(None),
        CategorizationSuggestion.reason != NO_AI_REASON,
        CategorizationSuggestion.category_id.is_not(None),
    )


def categorizable(workspace_id: uuid.UUID) -> list[Any]:
    """The rows categorization covers (its candidates, Revisar, Aplicadas by
    origin, the scoreboard): posted, not one side of a transfer between
    accounts, not an opening balance, not ignored."""
    return [
        Transaction.workspace_id == workspace_id,
        Transaction.status == "posted",
        Transaction.transfer_pair_id.is_(None),
        Transaction.source != "opening_balance",
        Transaction.is_ignored.is_(False),
    ]


async def collect_candidates(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    since: datetime | None = None,
    *,
    revisit_no_ai: bool = False,
) -> list[Candidate]:
    """Posted, unpaired, counted transactions whose category is still a guess.

    Left out: rows with a pending suggestion (already waiting for the
    person), and rows of a store whose suggestions the person dismissed
    after the row arrived -- "ignore" holds until a new purchase of that
    store lands. With `since`, only rows created from then on. With
    `revisit_no_ai` (a model is available), a pending suggestion made
    without one does not keep its row out: the model may decide it now.
    One the model itself left undecided still does, so the same row is not
    paid for twice. Rows a person cleared are flagged (`cleared`).
    """
    blocking = [CategorizationSuggestion.status == "pending"]
    if revisit_no_ai:
        blocking.append(blocking_suggestion())
    pending = exists().where(
        CategorizationSuggestion.transaction_id == Transaction.id,
        *blocking,
    )
    window = [Transaction.created_at >= since] if since is not None else []
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
            *categorizable(workspace_id),
            Transaction.category_source.in_(REVIEWABLE_SOURCES),
            ~pending,
            *window,
        )
        .order_by(Transaction.date, Transaction.id)
    )
    dismissed = await _dismissed_at(session, workspace_id)
    kept: list[tuple[Any, str]] = []
    for row in rows.all():
        key = merchant_key(row.description, payee_name=row.payee_name, payee_source=row.payee_source)
        dismissed_at = dismissed.get(stored_key(key))
        if dismissed_at is not None and (
            row.created_at is None or _utc(row.created_at) <= dismissed_at
        ):
            continue
        kept.append((row, key))
    cleared = await cleared_by_person(session, [
        row.id for row, _ in kept if row.category_id is None and row.category_source == "none"
    ])
    candidates: list[Candidate] = []
    for row, key in kept:
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
                cleared=row.id in cleared,
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


def _decide(
    hit: MemoryHit | None, threshold: int, assignable: set[uuid.UUID]
) -> _Decision | None:
    """What the memory settles for a store: the person's habit first.

    A category that is gone or hidden is no decision (the memory keeps ids
    in JSON, with no foreign key to stop them going stale).
    """
    if hit is None:
        return None
    if hit.consistent and hit.category_id is not None and hit.category_id in assignable:
        return _Decision(hit.category_id, "memory")
    if (
        hit.ai_category_id is not None
        and hit.ai_category_id in assignable
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
    above the workspace threshold applies as `ai`. Only rows in
    `AUTO_APPLY_SOURCES` change. One already at the settled category is
    settled all the same (a provider guess the memory confirms becomes
    `memory`, the event recording the confirmation): left a guess, it would
    stay a candidate and, once the memory changes, be paid for again. A
    settled store is consumed even when nothing changes. A row the person
    cleared (R27) is not changed: it gets a pending suggestion with the
    settled category, and is not sent to the model either. One a sync filed
    since it was collected is an ordinary guess again. `stats["memory"]`
    / `stats["applied"]` count the rows whose category changed,
    `stats["confirmed"]` the ones settled at the category they held,
    `stats["suggested"]` the cleared rows queued.
    """
    if not groups:
        return {}
    settings = await get_settings(session, workspace_id)
    hits = await lookup(session, workspace_id, list(groups))
    assignable = await get_assignable_category_ids(session, workspace_id)

    plan: dict[uuid.UUID, _Decision] = {}
    held: dict[uuid.UUID, str] = {}  # cleared row -> its store
    remaining: dict[str, MerchantGroup] = {}
    for key, group in groups.items():
        # The nameless "store" is no store: nothing it holds applies.
        decision = (
            _decide(hits.get(key), settings.auto_apply_threshold, assignable)
            if teaches(key)
            else None
        )
        if decision is None:
            remaining[key] = group
            continue
        for cand in group.candidates:
            if cand.current_source in AUTO_APPLY_SOURCES:
                plan[cand.tx_id] = decision
                if cand.cleared:
                    held[cand.tx_id] = key

    staged: list[StagedChange] = []
    suggestions: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for ids in _chunks(list(plan)):
        txs = await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id, Transaction.id.in_(ids)
            )
        )
        for tx in txs.scalars().all():
            # The row may have moved on since it was collected.
            if tx.category_source not in AUTO_APPLY_SOURCES:
                continue
            decision = plan[tx.id]
            # Still empty: a sync may have filed the row since it was collected.
            if tx.id in held and tx.category_id is None:
                suggestions.append({
                    "id": uuid.uuid4(),
                    "workspace_id": workspace_id,
                    "transaction_id": tx.id,
                    "run_id": run_id,
                    "merchant_key": stored_key(held[tx.id]),
                    "status": "pending",
                    "category_id": decision.category_id,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "created_at": now,
                })
                continue
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
    await supersede_no_ai(
        session, workspace_id,
        [change.tx.id for change in staged] + [row["transaction_id"] for row in suggestions],
    )
    if suggestions:
        await session.execute(sa_insert(CategorizationSuggestion), suggestions)

    if stats is not None:
        if suggestions:
            stats["suggested"] = stats.get("suggested", 0) + len(suggestions)
        for change in staged:
            if change.previous_category_id == change.new_category_id:
                counter = "confirmed"
            else:
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

    The suggestion carries no category (there is no model to propose one)
    and `reason=NO_AI_REASON`, so a run with a model can revisit it.
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
            "reason": NO_AI_REASON,
            "created_at": now,
        }
        for tx_id, key in wanted
        if tx_id not in already
    ]
    if payload:
        await session.execute(sa_insert(CategorizationSuggestion), payload)
    return len(payload)


async def supersede_no_ai(
    session: AsyncSession, workspace_id: uuid.UUID, tx_ids: list[uuid.UUID]
) -> None:
    """Close the model-less pending suggestions of rows just decided.

    Marked `dismissed` with `SUPERSEDED_REASON`, which `_dismissed_at` does
    not count as the person ignoring the store.
    """
    now = datetime.now(timezone.utc)
    for ids in _chunks(list(dict.fromkeys(tx_ids))):
        await session.execute(
            sa_update(CategorizationSuggestion)
            .where(
                CategorizationSuggestion.workspace_id == workspace_id,
                CategorizationSuggestion.transaction_id.in_(ids),
                CategorizationSuggestion.status == "pending",
                model_less_suggestion(),
            )
            .values(status="dismissed", reason=SUPERSEDED_REASON, resolved_at=now)
            .execution_options(synchronize_session="fetch")
        )


def close_stats(stats: dict[str, Any]) -> None:
    """`skipped` = candidates neither changed nor queued for the person."""
    stats["skipped"] = max(
        0,
        stats.get("candidates", 0)
        - stats.get("memory", 0)
        - stats.get("confirmed", 0)
        - stats.get("applied", 0)
        - stats.get("suggested", 0),
    )


async def suggest_for_run(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    groups: dict[str, MerchantGroup],
    run: CategorizationRun,
) -> None:
    """The model-less ending of a run: `groups` become suggestions and the
    run's counters close."""
    stats = dict(run.stats or {})
    stats["suggested"] = stats.get("suggested", 0) + await suggest_without_ai(
        session, workspace_id, groups, run.id
    )
    close_stats(stats)
    run.stats = stats


def _undecided(groups: dict[str, MerchantGroup]) -> dict[str, MerchantGroup]:
    """Drop rows the pipeline itself already settled (and still stand)."""
    return group_by_key(
        cand
        for group in groups.values()
        for cand in group.candidates
        if not (cand.current_source in PIPELINE_SOURCES and cand.current_category_id is not None)
    )


async def _ai_available(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    settings = await get_settings(session, workspace_id)
    return await find_ai_connection(session, settings) is not None


@dataclass
class PreparedRun:
    """A run after its free phase: the stores the memory could not settle."""

    run: CategorizationRun
    remaining: dict[str, MerchantGroup]
    stats: dict[str, Any]


async def prepare_run(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    trigger: str,
    run: CategorizationRun | None = None,
    use_ai: bool = False,
) -> PreparedRun:
    """Phase 1: candidates, stores, memory. Cheap and local.

    Uses `run` when the caller already created it (a queued manual run),
    otherwise creates one. An `ingest` run looks only at rows since the
    previous finished run (`ingest_since`); `manual` and `backlog` look at
    everything. When the run may use a model and one is available, rows
    waiting on a model-less suggestion are candidates again.
    """
    since = await ingest_since(session, workspace_id) if trigger == "ingest" else None
    revisit = (
        use_ai and ai_classifier is not None and await _ai_available(session, workspace_id)
    )
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

    candidates = await collect_candidates(
        session, workspace_id, since=since, revisit_no_ai=revisit
    )
    groups = group_by_key(candidates)
    stats: dict[str, Any] = {
        "candidates": len(candidates),
        "groups": len(groups),
        "memory": 0,
        "confirmed": 0,
        "applied": 0,
        "suggested": 0,
        "skipped": 0,
    }
    remaining = _undecided(await memory_pass(session, workspace_id, groups, run.id, stats=stats))
    run.stats = dict(stats)
    await session.flush()
    return PreparedRun(run=run, remaining=remaining, stats=stats)


async def complete_run(
    session: AsyncSession, workspace_id: uuid.UUID, prepared: PreparedRun, *, use_ai: bool
) -> CategorizationRun:
    """Phase 2: the classifier (`ai_classifier`) or, without one, suggestions.

    The classifier gets the remaining stores and the run, whose `stats`
    already hold the free phase's counters; it owns the rest of them
    (reassigning `run.stats`) and may leave the run `capped` or
    `processing_batch`.
    """
    run = prepared.run
    # `prepare_run` already stored these; only write the row when they
    # differ (the batch path must take the workspace lock before any write
    # to this row, see `classifier._submit_batch`).
    if run.stats != prepared.stats:
        run.stats = dict(prepared.stats)
    classifier = ai_classifier
    if use_ai and classifier is not None:
        await classifier(session, workspace_id, prepared.remaining, run)
    else:
        await suggest_for_run(session, workspace_id, prepared.remaining, run)

    now = datetime.now(timezone.utc)
    if run.status == "running":
        run.status = "done"
    if run.status in FINISHED_STATUSES:
        run.finished_at = now
    settings = await get_settings(session, workspace_id)
    settings.last_run_at = now
    await session.flush()
    return run


async def run_categorization(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    trigger: str,
    use_ai: bool,
    run: CategorizationRun | None = None,
) -> CategorizationRun:
    """One pass of the pipeline (both phases), recorded on a `classify` run.

    Without a classifier (or with `use_ai` off) the stores the memory could
    not settle become pending suggestions.
    """
    prepared = await prepare_run(
        session, workspace_id, trigger=trigger, run=run, use_ai=use_ai
    )
    return await complete_run(session, workspace_id, prepared, use_ai=use_ai)
