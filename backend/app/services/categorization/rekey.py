"""Re-keying stores from the v1 description rule to v2 (spec §5, decision 5).

The v2 rule of `merchant_key` drops a short acquirer prefix before the "*"
("Mlp*Netshoes" was the store "MLP", now "NETSHOES"). Rows keyed under v1
would otherwise sit apart from the stores the app now computes: a pending
suggestion grouped under "MLP", a memory row "MLP" nobody looks up again.
This module rewrites them once, at deploy, after `alembic upgrade head`
(`scripts/rekey_merchant_keys.py`).

Per workspace, under the workspace lock, and only when no `classify` or
`redistribute` run is busy (those write suggestions and memory; a busy
workspace is skipped and reported, to be run again later):

- **Suggestions** (every status: a dismissed one is what keeps a store
  "ignored") keyed with the v1 key of their transaction, when v2 differs,
  take the v2 key. A key the old rule did not produce (a payee set since,
  say) is left alone: rewriting it would move the pick an undo takes back
  to a key that never got it.
- **Memory**, per row `K` that some transaction had as its v1 key. Its
  destinations are the v2 keys of those transactions, plus `K` itself when
  some transaction still has `K` as its v2 key. A row whose only
  destination is itself stays as it is; so does a row no transaction leads
  to.

  - One destination `N` -> **merge**: `counts` summed into `N` (`K` is
    renamed when `N` does not exist), `last_user_category_id` of the row
    updated last, the newest note, `ai_category_id`/`ai_confidence` dropped
    (the store changed), `ai_clean_name` of `N`, else of `K`; `K` goes.
  - Two or more -> **split** (`K` mixed different stores): `K`'s counts are
    unusable, so each destination is re-seeded from the person's own rows
    (`category_source == "user"`) that now carry its key. One pick per
    decision, per category (R16): rows a person filed together are one
    decision, told apart by the second of the standing `user` event that
    gave each row its category; a row with no such event (older than the
    events) is a decision of its own. A destination the memory already
    knew keeps, per category, the larger of what it had and what the rows
    show, so a second run finds nothing to add. `K` goes, unless it is
    itself a destination: then it is re-seeded like the others, its note
    kept. `last_user_category_id` is the category of the latest decision; a
    re-seeded row loses its cached AI decision.

Undo after the re-key: events keep no key. Undoing a person's change takes
its pick back from the key computed at undo time (`memory.keys_for`, v2),
or from the accepted suggestion's key, which this re-key rewrote too; a
merged or re-seeded row holds that pick, so the count of the new key drops
by one. Picks a person made under a mixed key, undone after a split, come
out of the destination re-seeded from that row.

Rerunning changes nothing: merged and split-away keys are gone (no
transaction produces them), and a re-seed of the same rows gives the same
counts. Nothing here commits, except `rekey_all`, once per workspace.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
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


@dataclass(frozen=True)
class KeyChange:
    """One memory row re-keyed: `old` merged into, or split among, `new`."""

    kind: str
    old: str
    new: tuple[str, ...]
    #: Picks the old row held.
    picks: int
    had_note: bool


@dataclass
class RekeyReport:
    workspace_id: uuid.UUID
    suggestions: int = 0
    merged: int = 0
    split: int = 0
    seeded: int = 0
    skipped: str | None = None
    changes: list[KeyChange] = field(default_factory=list)


@dataclass(frozen=True)
class _Row:
    id: uuid.UUID
    old: str
    new: str
    category_id: uuid.UUID | None
    source: str
    day: date


@dataclass
class _Seed:
    #: category id -> distinct decisions.
    decisions: dict[str, set[Any]] = field(default_factory=lambda: defaultdict(set))
    latest: tuple[Any, ...] | None = None
    last: uuid.UUID | None = None

    def counts(self) -> dict[str, int]:
        return {cat: len(found) for cat, found in self.decisions.items()}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _picks(row: MerchantMemory) -> int:
    return sum(memory._counts(row.counts).values())


async def _busy(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    from app.tasks import categorization_tasks as tasks

    run_id = await session.scalar(
        select(CategorizationRun.id)
        .where(
            CategorizationRun.workspace_id == workspace_id,
            CategorizationRun.kind.in_(BUSY_KINDS),
            CategorizationRun.status.in_(tasks.BUSY_STATUSES),
        )
        .limit(1)
    )
    return run_id is not None


def _key(
    description: str | None, payee_name: str | None, payee_source: str | None, version: int
) -> str:
    return memory.stored_key(
        merchant_key(
            description, payee_name=payee_name, payee_source=payee_source, version=version
        )
    )


async def _load_rows(session: AsyncSession, workspace_id: uuid.UUID) -> list[_Row]:
    """Every transaction with its v1 and v2 stored key, the payee read the
    way `memory.keys_for` reads it."""
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
        .outerjoin(Payee, Payee.id == Transaction.payee_id)
        .where(Transaction.workspace_id == workspace_id)
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
    session: AsyncSession, workspace_id: uuid.UUID, rows: list[_Row]
) -> int:
    by_tx = {row.id: row for row in rows if row.old != row.new}
    if not by_tx:
        return 0
    suggestions = (
        await session.execute(
            select(CategorizationSuggestion).where(
                CategorizationSuggestion.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    moved = 0
    for sug in suggestions:
        row = by_tx.get(sug.transaction_id)
        if row is not None and sug.merchant_key == row.old:
            sug.merchant_key = row.new
            moved += 1
    return moved


async def _seeds(
    session: AsyncSession, workspace_id: uuid.UUID, rows: list[_Row], keys: set[str]
) -> dict[str, _Seed]:
    """Per store key in `keys`, the person's decisions behind its `user` rows."""
    chosen = [
        row for row in rows
        if row.new in keys and row.source == "user" and row.category_id is not None
    ]
    decided_at: dict[uuid.UUID, datetime] = {}
    wanted = {row.id: row.category_id for row in chosen}
    ids = list(wanted)
    for start in range(0, len(ids), memory.CHUNK):
        events = await session.execute(
            select(
                CategorizationEvent.transaction_id,
                CategorizationEvent.new_category_id,
                CategorizationEvent.created_at,
            ).where(
                CategorizationEvent.workspace_id == workspace_id,
                CategorizationEvent.transaction_id.in_(ids[start:start + memory.CHUNK]),
                CategorizationEvent.source == "user",
                CategorizationEvent.undone_at.is_(None),
            )
        )
        for tx_id, category_id, created_at in events.all():
            at = _utc(created_at)
            # The standing event that gave the row its current category.
            if at is None or category_id != wanted[tx_id]:
                continue
            if tx_id not in decided_at or at > decided_at[tx_id]:
                decided_at[tx_id] = at

    seeds: dict[str, _Seed] = {}
    for row in chosen:
        assert row.category_id is not None
        seed = seeds.setdefault(row.new, _Seed())
        at = decided_at.get(row.id)
        # Rows filed in the same second are one decision; a row with no
        # event is one on its own.
        decision = ("at", int(at.timestamp())) if at is not None else ("row", row.id)
        seed.decisions[str(row.category_id)].add(decision)
        order = (at is not None, at or datetime.min.replace(tzinfo=timezone.utc), row.day)
        if seed.latest is None or order > seed.latest:
            seed.latest = order
            seed.last = row.category_id
    return seeds


def _merge_into(target: MerchantMemory, source: MerchantMemory) -> None:
    counts = memory._counts(target.counts)
    for category, picks in memory._counts(source.counts).items():
        counts[category] = counts.get(category, 0) + picks
    target.counts = counts  # reassigned: in-place JSON mutation is not tracked
    floor = datetime.min.replace(tzinfo=timezone.utc)
    newer, older = sorted(
        (target, source), key=lambda r: _utc(r.updated_at) or floor, reverse=True
    )
    target.last_user_category_id = newer.last_user_category_id or older.last_user_category_id
    noted = [r for r in (target, source) if r.note is not None]
    if noted:
        latest = max(noted, key=lambda r: _utc(r.note_updated_at) or floor)
        target.note, target.note_updated_at = latest.note, latest.note_updated_at
    target.ai_clean_name = target.ai_clean_name or source.ai_clean_name
    target.ai_category_id = None
    target.ai_confidence = None


async def _rekey_memory(
    session: AsyncSession, workspace_id: uuid.UUID, rows: list[_Row], report: RekeyReport
) -> None:
    mapping: dict[str, set[str]] = defaultdict(set)
    current: set[str] = set()
    for row in rows:
        mapping[row.old].add(row.new)
        current.add(row.new)
    stored = {
        r.merchant_key: r
        for r in (
            await session.execute(
                select(MerchantMemory).where(MerchantMemory.workspace_id == workspace_id)
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

    for old, new in merges:
        source = stored.pop(old)
        report.changes.append(KeyChange(MERGE, old, (new,), _picks(source), source.note is not None))
        report.merged += 1
        target = stored.get(new)
        if target is None:
            source.merchant_key = new
            source.ai_category_id = None
            source.ai_confidence = None
            stored[new] = source
        else:
            _merge_into(target, source)
            await session.delete(source)
        await session.flush()

    if not splits:
        return
    before = {key: (_picks(stored[key]), stored[key].note is not None) for key in splits}
    targets: set[str] = set().union(*splits.values())
    seeds = await _seeds(session, workspace_id, rows, targets)
    changed: set[str] = set()
    for key in sorted(targets):
        seed = seeds.get(key) or _Seed()
        seeded = seed.counts()
        row = stored.get(key)
        # A key that mixed stores is rebuilt from its rows alone.
        base = {} if key in splits or row is None else memory._counts(row.counts)
        counts = dict(base)
        for category, picks in seeded.items():
            counts[category] = max(counts.get(category, 0), picks)
        if seeded:
            last = seed.last
        elif key in splits or row is None:
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
        else:
            continue
        changed.add(key)
        report.seeded += 1

    for key in sorted(splits):
        destinations = splits[key]
        if key not in current:
            await session.delete(stored.pop(key))
        elif not destinations & changed:
            continue  # a key still in use, already re-seeded: nothing to do
        report.split += 1
        report.changes.append(KeyChange(SPLIT, key, tuple(sorted(destinations)), *before[key]))
    await session.flush()


async def rekey_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, *, dry_run: bool = False
) -> RekeyReport:
    """Re-key one workspace (see the module docstring); the caller commits.

    Takes the workspace lock first, so no run is claimed meanwhile; a
    workspace with a busy `classify`/`redistribute` run is reported
    `skipped="busy"` untouched. `dry_run` computes the report and rolls the
    work back.
    """
    from app.tasks import categorization_tasks as tasks

    report = RekeyReport(workspace_id)
    await tasks.lock_workspace(session, workspace_id)
    if await _busy(session, workspace_id):
        report.skipped = SKIPPED_BUSY
    else:
        rows = await _load_rows(session, workspace_id)
        report.suggestions = await _rekey_suggestions(session, workspace_id, rows)
        await _rekey_memory(session, workspace_id, rows, report)
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
