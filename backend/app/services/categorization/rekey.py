"""Re-keying stores from the v1 description rule to v2 (spec §5, decision 5).

The v2 rule of `merchant_key` drops a short acquirer prefix before the "*"
("Mlp*Netshoes" was the store "MLP", now "NETSHOES"). Rows keyed under v1
would otherwise sit apart from the stores the app now computes: a pending
suggestion grouped under "MLP", a memory row "MLP" nobody looks up again.
This module rewrites them once, at deploy, after `alembic upgrade head`
(`scripts/rekey_merchant_keys.py`).

Per workspace, under the workspace lock, and only when no `classify` or
`redistribute` run is busy (those write suggestions and memory; a busy
workspace is skipped and reported with the run that holds it, to be run
again later). The transactions considered are the pipeline's candidates
(posted, not a transfer pair, not an opening balance, not ignored), their
payee read within the workspace, as the pipeline reads it. A key is *dead*
when some transaction had it under v1 and none has it under v2.

- **Suggestions** (every status: a dismissed one is what keeps a store
  "ignored") keyed with the v1 key of their transaction, when v2 differs,
  take the v2 key. One keyed with a dead key otherwise (a payee set since,
  say) follows the memory of that key: to the key it merges into, else to
  its transaction's v2 key. Any other key is left alone: rewriting it would
  move the pick an undo takes back to a key that never got it.
- **Organizer proposals** still pending (`organize` runs, `create_category`
  ops not decided) name each store by key; a dead one takes the v2 key most
  of the store's transactions now have, so approving it later repoints the
  memory that exists. A store that mixed several (`MLP`) repoints only that
  one; its rows all move as before.
- **Memory**, per row `K` that some transaction had as its v1 key. Its
  destinations are the v2 keys of those transactions, plus `K` itself when
  some transaction still has `K` as its v2 key. A row whose only
  destination is itself stays as it is; so does a row no transaction leads
  to.

  - One destination `N` -> **merge**: `counts` summed into `N` (`K` is
    renamed when `N` does not exist); the last choice is that of the
    person's latest decision on the store's rows (else that of the row
    updated last); the newest note (the other one is reported, its text
    printed, so nothing the person wrote vanishes unseen);
    `ai_category_id`/`ai_confidence` dropped (the store changed);
    `ai_clean_name` of `N`, else of `K`; `K` goes.
  - Two or more -> **split** (`K` mixed different stores): `K`'s counts are
    unusable, so each destination is re-seeded from the person's own rows
    (`category_source == "user"`) that now carry its key. One pick per
    decision, per category (R16): a row's decision is the standing `user`
    event that gave it its category; events of one store, category and
    person less than `DECISION_GAP` apart are one decision (one bulk
    write), and a row with no such event (older than the events) is a
    decision of its own. A destination the memory already knew keeps, per
    category, the larger of what it had and what the rows show: its own
    rows are among those counted, and a sum would count them twice (and
    again on every run). The last choice is that of the latest decision. A
    re-seeded row loses its cached AI decision. `K` goes, unless it is
    itself a destination: then it is rebuilt like the others, and also
    loses its clean name and its note (both described the mixed store; the
    note text is reported). Notes are never copied to the destinations: a
    note goes into the prompt, and one about the mixed store would mislead
    the model about every store it held.

Undo after the re-key. Events keep no key: undoing a person's change takes
its pick back from the key computed at undo time (`memory.keys_for`, v2),
or from the accepted suggestion's key, rewritten above. The pick is taken
only by the event marked `memory_pick`, and a bulk decision under a mixed
key marked one event, in one of the stores it covered. So each decision a
re-seed counts gets one marked event among its rows (when none has one):
undoing the bulk then takes back, store by store, exactly what the re-seed
gave, and no store keeps a pick the undo left behind. A merge needs no
marking: the picks move with the counts.

Rerunning changes nothing: dead keys are gone from memory, suggestions and
proposals; a re-seed of the same rows gives the same counts, finds its
events marked and leaves a row it would not change untouched. The cost of
that: a key still in use that mixed stores is recognised as such on every
run, so if its row's counts already equal its re-seed on the first run, its
cached AI decision and note stay. Nothing here commits, except
`rekey_all`, once per workspace.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.categorization import memory
from app.services.categorization.merchant_key import merchant_key

OLD_VERSION = 1
NEW_VERSION = 2
#: Run kinds that write suggestions or memory; one busy holds the re-key back.
BUSY_KINDS = ("classify", "redistribute")
SKIPPED_BUSY = "busy"
MERGE = "merge"
SPLIT = "split"
#: Events of one store, category and person closer than this are one
#: decision: a bulk write stamps each row as it goes.
DECISION_GAP = timedelta(seconds=2)
_FLOOR = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class KeyChange:
    """One memory row re-keyed: `old` merged into, or split among, `new`."""

    kind: str
    old: str
    new: tuple[str, ...]
    #: Picks the old row held.
    picks: int
    #: Note text the change dropped (the older of two merged, a split's).
    notes: tuple[str, ...] = ()


@dataclass
class RekeyReport:
    workspace_id: uuid.UUID
    suggestions: int = 0
    merged: int = 0
    split: int = 0
    seeded: int = 0
    skipped: str | None = None
    #: Events marked `memory_pick` for the decisions a re-seed counted.
    marked: int = 0
    #: Stores of pending organizer proposals re-keyed.
    proposals: int = 0
    #: The run that made the workspace busy (kind, status, id, age).
    busy_run: str | None = None
    changes: list[KeyChange] = field(default_factory=list)


@dataclass(frozen=True)
class _Row:
    id: uuid.UUID
    old: str
    new: str
    category_id: uuid.UUID | None
    source: str
    day: date


@dataclass(frozen=True)
class _Event:
    id: uuid.UUID
    at: datetime
    user_id: uuid.UUID | None
    memory_pick: bool


@dataclass
class _Decision:
    category: str
    events: list[_Event]
    day: date

    @property
    def order(self) -> tuple[bool, datetime, date]:
        # A decision with an event is newer than any without (those predate
        # the events), then by time, then by the row's date.
        at = self.events[-1].at if self.events else None
        return (at is not None, at or _FLOOR, self.day)


@dataclass
class _Seed:
    decisions: list[_Decision] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return dict(Counter(d.category for d in self.decisions))

    def latest(self) -> _Decision | None:
        if not self.decisions:
            return None
        return max(self.decisions, key=lambda d: d.order)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _picks(row: MerchantMemory) -> int:
    return sum(memory._counts(row.counts).values())


def _age(created_at: datetime | None) -> str:
    at = _utc(created_at)
    if at is None:
        return "?"
    minutes = max(0, int((datetime.now(timezone.utc) - at).total_seconds() // 60))
    return f"{minutes // 60}h{minutes % 60:02d}m"


async def _busy_run(session: AsyncSession, workspace_id: uuid.UUID) -> str | None:
    from app.tasks import categorization_tasks as tasks

    run = (
        await session.execute(
            select(
                CategorizationRun.id,
                CategorizationRun.kind,
                CategorizationRun.status,
                CategorizationRun.created_at,
            )
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind.in_(BUSY_KINDS),
                CategorizationRun.status.in_(tasks.BUSY_STATUSES),
            )
            .order_by(CategorizationRun.created_at)
            .limit(1)
        )
    ).first()
    if run is None:
        return None
    run_id, kind, status, created_at = run
    return f"{kind} {status} {run_id}, criada há {_age(created_at)}"


def _key(
    description: str | None, payee_name: str | None, payee_source: str | None, version: int
) -> str:
    return memory.stored_key(
        merchant_key(
            description, payee_name=payee_name, payee_source=payee_source, version=version
        )
    )


async def _load_rows(session: AsyncSession, workspace_id: uuid.UUID) -> list[_Row]:
    """The pipeline's candidate transactions with their v1 and v2 stored
    key, the payee read within the workspace as the pipeline reads it."""
    result = await session.execute(
        select(
            Transaction.id,
            Transaction.description,
            Payee.name,
            Payee.source,
            Transaction.category_id,
            Transaction.category_source,
            Transaction.date,
        )
        .outerjoin(
            Payee, and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.status == "posted",
            Transaction.transfer_pair_id.is_(None),
            Transaction.source != "opening_balance",
            Transaction.is_ignored.is_(False),
        )
    )
    cache: dict[tuple[str | None, str | None, str | None], tuple[str, str]] = {}
    rows: list[_Row] = []
    for tx_id, description, payee_name, payee_source, category_id, source, day in result.all():
        names = (description, payee_name, payee_source)
        keys = cache.get(names)
        if keys is None:
            keys = cache[names] = (
                _key(description, payee_name, payee_source, OLD_VERSION),
                _key(description, payee_name, payee_source, NEW_VERSION),
            )
        old, new = keys
        rows.append(_Row(tx_id, old, new, category_id, source, day))
    return rows


async def _rekey_suggestions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    by_tx: dict[uuid.UUID, _Row],
    dead: dict[str, str | None],
) -> int:
    suggestions = (
        await session.execute(
            select(CategorizationSuggestion)
            .where(CategorizationSuggestion.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalars().all()
    moved = 0
    for sug in suggestions:
        row = by_tx.get(sug.transaction_id)
        if row is not None and sug.merchant_key == row.old and row.old != row.new:
            target: str | None = row.new
        elif sug.merchant_key in dead:
            target = dead[sug.merchant_key] or (row.new if row is not None else None)
        else:
            continue
        if target is not None and target != sug.merchant_key:
            sug.merchant_key = target
            moved += 1
    return moved


async def _rekey_proposals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    by_tx: dict[uuid.UUID, _Row],
    dead: dict[str, str | None],
) -> int:
    """Re-key the stores named by organizer ops still waiting for a decision."""
    runs = (
        await session.execute(
            select(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "organize",
                CategorizationRun.status == "done",
            )
            .with_for_update()
        )
    ).scalars().all()
    moved = 0
    for run in runs:
        detail = dict(run.detail or {})
        stores = detail.get("stores")
        if not isinstance(stores, dict):
            continue
        pending = {
            short
            for op in detail.get("ops") or []
            if isinstance(op, dict) and op.get("status") == "pending"
            for short in op.get("children") or []
        }
        rewritten = dict(stores)
        for short in sorted(pending):
            info = stores.get(short)
            key = info.get("key") if isinstance(info, dict) else None
            if not isinstance(info, dict) or not isinstance(key, str):
                continue
            if memory.stored_key(key) not in dead:
                continue
            votes: Counter[str] = Counter()
            for raw in info.get("tx") or []:
                try:
                    row = by_tx.get(uuid.UUID(str(raw)))
                except ValueError:
                    continue
                if row is not None:
                    votes[row.new] += 1
            ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
            target = ranked[0][0] if ranked else dead[memory.stored_key(key)]
            if target is None or target == key:
                continue
            rewritten[short] = {**info, "key": target}
            moved += 1
        if rewritten != stores:
            run.detail = {**detail, "stores": rewritten}  # reassigned: JSON is not tracked
    return moved


async def _seeds(
    session: AsyncSession, workspace_id: uuid.UUID, rows: list[_Row], keys: set[str]
) -> dict[str, _Seed]:
    """Per store key in `keys`, the person's decisions behind its `user` rows."""
    chosen = [
        row for row in rows
        if row.new in keys and row.source == "user" and row.category_id is not None
    ]
    wanted = {row.id: row.category_id for row in chosen}
    decided: dict[uuid.UUID, _Event] = {}
    ids = list(wanted)
    for start in range(0, len(ids), memory.CHUNK):
        found = await session.execute(
            select(
                CategorizationEvent.id,
                CategorizationEvent.transaction_id,
                CategorizationEvent.new_category_id,
                CategorizationEvent.created_at,
                CategorizationEvent.user_id,
                CategorizationEvent.memory_pick,
            ).where(
                CategorizationEvent.workspace_id == workspace_id,
                CategorizationEvent.transaction_id.in_(ids[start:start + memory.CHUNK]),
                CategorizationEvent.source == "user",
                CategorizationEvent.undone_at.is_(None),
            )
        )
        for ev_id, tx_id, category_id, created_at, user_id, pick in found.all():
            at = _utc(created_at)
            # The standing event that gave the row its current category.
            if at is None or category_id != wanted[tx_id]:
                continue
            if tx_id not in decided or at > decided[tx_id].at:
                decided[tx_id] = _Event(ev_id, at, user_id, bool(pick))

    seeds: dict[str, _Seed] = defaultdict(_Seed)
    bursts: dict[tuple[str, str, uuid.UUID | None], list[tuple[_Event, date]]] = defaultdict(list)
    for row in chosen:
        category = str(row.category_id)
        event = decided.get(row.id)
        if event is None:
            seeds[row.new].decisions.append(_Decision(category, [], row.day))
        else:
            bursts[(row.new, category, event.user_id)].append((event, row.day))
    for (key, category, _user), found_events in bursts.items():
        found_events.sort(key=lambda pair: (pair[0].at, pair[0].id))
        current: _Decision | None = None
        for event, day in found_events:
            if current is None or event.at - current.events[-1].at > DECISION_GAP:
                current = _Decision(category, [], day)
                seeds[key].decisions.append(current)
            current.events.append(event)
            current.day = max(current.day, day)
    return dict(seeds)


def _merge_into(target: MerchantMemory, source: MerchantMemory, seed: _Seed | None) -> list[str]:
    """Fold `source` into `target`; returns the note text dropped."""
    counts = memory._counts(target.counts)
    for category, picks in memory._counts(source.counts).items():
        counts[category] = counts.get(category, 0) + picks
    target.counts = counts  # reassigned: in-place JSON mutation is not tracked
    latest = seed.latest() if seed is not None else None
    if latest is not None and latest.events:
        target.last_user_category_id = uuid.UUID(latest.category)
    else:
        newer, older = sorted(
            (target, source), key=lambda r: _utc(r.updated_at) or _FLOOR, reverse=True
        )
        target.last_user_category_id = newer.last_user_category_id or older.last_user_category_id
    dropped: list[str] = []
    noted = sorted(
        (r for r in (target, source) if r.note is not None),
        key=lambda r: _utc(r.note_updated_at) or _FLOOR,
        reverse=True,
    )
    if noted:
        kept = noted[0]
        dropped = [r.note for r in noted[1:] if r.note is not None and r.note != kept.note]
        target.note, target.note_updated_at = kept.note, kept.note_updated_at
    target.ai_clean_name = target.ai_clean_name or source.ai_clean_name
    target.ai_category_id = None
    target.ai_confidence = None
    return dropped


async def _mark_picks(session: AsyncSession, seeds: list[_Seed]) -> int:
    """One `memory_pick` per counted decision, where none of its events has
    one: an undo takes back exactly the picks the re-seed gave."""
    ids = [
        decision.events[0].id
        for seed in seeds
        for decision in seed.decisions
        if decision.events and not any(ev.memory_pick for ev in decision.events)
    ]
    for start in range(0, len(ids), memory.CHUNK):
        await session.execute(
            sa_update(CategorizationEvent)
            .where(CategorizationEvent.id.in_(ids[start:start + memory.CHUNK]))
            .values(memory_pick=True)
            .execution_options(synchronize_session=False)
        )
    return len(ids)


async def _rekey_memory(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[_Row],
    stored: dict[str, MerchantMemory],
    merges: list[tuple[str, str]],
    splits: dict[str, set[str]],
    report: RekeyReport,
) -> None:
    current = {row.new for row in rows}
    targets: set[str] = set().union(*splits.values()) if splits else set()
    seeds = await _seeds(
        session, workspace_id, rows, targets | {new for _old, new in merges}
    )

    for old, new in merges:
        source = stored.pop(old)
        picks = _picks(source)
        target = stored.get(new)
        dropped: list[str] = []
        if target is None:
            latest = seeds[new].latest() if new in seeds else None
            if latest is not None and latest.events:
                source.last_user_category_id = uuid.UUID(latest.category)
            source.merchant_key = new
            source.ai_category_id = None
            source.ai_confidence = None
            stored[new] = source
        else:
            dropped = _merge_into(target, source, seeds.get(new))
            await session.delete(source)
        await session.flush()
        report.merged += 1
        report.changes.append(KeyChange(MERGE, old, (new,), picks, tuple(dropped)))

    if not splits:
        return
    before = {key: _picks(stored[key]) for key in splits}
    notes: dict[str, str] = {}
    changed: set[str] = set()
    for key in sorted(targets):
        seed = seeds.get(key) or _Seed()
        seeded = seed.counts()
        row = stored.get(key)
        mixed = key in splits
        # A key that mixed stores is rebuilt from its rows alone.
        base = {} if mixed or row is None else memory._counts(row.counts)
        counts = dict(base)
        for category, picks in seeded.items():
            counts[category] = max(counts.get(category, 0), picks)
        latest = seed.latest()
        if latest is not None:
            last: uuid.UUID | None = uuid.UUID(latest.category)
        elif mixed or row is None:
            last = None
        else:
            last = row.last_user_category_id
        if row is None:
            if not counts:
                continue
            stored[key] = MerchantMemory(
                id=uuid.uuid4(), workspace_id=workspace_id, merchant_key=key,
                counts=counts, last_user_category_id=last,
            )
            session.add(stored[key])
        elif counts != memory._counts(row.counts) or last != row.last_user_category_id:
            row.counts = counts
            row.last_user_category_id = last
            row.ai_category_id = None
            row.ai_confidence = None
            if mixed:
                # The clean name and the note described the mixed store.
                row.ai_clean_name = None
                if row.note is not None:
                    notes[key] = row.note
                row.note = None
                row.note_updated_at = None
        else:
            continue
        changed.add(key)
        report.seeded += 1
    report.marked = await _mark_picks(
        session, [seeds[key] for key in sorted(targets) if key in seeds]
    )

    for key in sorted(splits):
        destinations = splits[key]
        if key not in current:
            row = stored.pop(key)
            if row.note is not None:
                notes[key] = row.note
            await session.delete(row)
        elif not destinations & changed:
            continue  # a key still in use, already rebuilt: nothing to do
        report.split += 1
        report.changes.append(
            KeyChange(
                SPLIT, key, tuple(sorted(destinations)), before[key],
                (notes[key],) if key in notes else (),
            )
        )
    await session.flush()


async def rekey_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, *, dry_run: bool = False
) -> RekeyReport:
    """Re-key one workspace (see the module docstring); the caller commits.

    Takes the workspace lock first, so no run is claimed meanwhile, and locks
    the memory, suggestion and proposal rows it reads; a workspace with a
    busy `classify`/`redistribute` run is reported `skipped="busy"`
    untouched. `dry_run` computes the report and rolls the work back.
    """
    from app.tasks import categorization_tasks as tasks

    report = RekeyReport(workspace_id)
    await tasks.lock_workspace(session, workspace_id)
    busy = await _busy_run(session, workspace_id)
    if busy is not None:
        report.skipped = SKIPPED_BUSY
        report.busy_run = busy
    else:
        rows = await _load_rows(session, workspace_id)
        by_tx = {row.id: row for row in rows}
        mapping: dict[str, set[str]] = defaultdict(set)
        current: set[str] = set()
        for row in rows:
            mapping[row.old].add(row.new)
            current.add(row.new)
        dead: dict[str, str | None] = {
            key: (next(iter(news)) if len(news) == 1 else None)
            for key, news in mapping.items()
            if key not in current
        }
        stored = {
            r.merchant_key: r
            for r in (
                await session.execute(
                    select(MerchantMemory)
                    .where(MerchantMemory.workspace_id == workspace_id)
                    .with_for_update()
                )
            ).scalars().all()
        }
        merges: list[tuple[str, str]] = []
        splits: dict[str, set[str]] = {}
        for key in sorted(stored):
            if key not in mapping:
                continue
            destinations = mapping[key] | ({key} & current)
            if destinations == {key}:
                continue
            if len(destinations) == 1:
                merges.append((key, next(iter(destinations))))
            else:
                splits[key] = destinations

        report.suggestions = await _rekey_suggestions(session, workspace_id, by_tx, dead)
        report.proposals = await _rekey_proposals(session, workspace_id, by_tx, dead)
        await _rekey_memory(session, workspace_id, rows, stored, merges, splits, report)
        await session.flush()
    if dry_run:
        await session.rollback()
    return report


async def rekey_all(
    session_maker: Any, *, dry_run: bool = False, workspace_id: uuid.UUID | None = None
) -> list[RekeyReport]:
    """`rekey_workspace` for every workspace (or only `workspace_id`), each in
    its own transaction and commit, so one busy or failing workspace leaves
    the ones before it done."""
    if workspace_id is not None:
        ids = [workspace_id]
    else:
        async with session_maker() as session:
            ids = list(
                (await session.execute(select(Workspace.id).order_by(Workspace.id))).scalars()
            )
    reports: list[RekeyReport] = []
    for ws_id in ids:
        async with session_maker() as session:
            report = await rekey_workspace(session, ws_id, dry_run=dry_run)
            if not dry_run:
                await session.commit()
        reports.append(report)
    return reports
