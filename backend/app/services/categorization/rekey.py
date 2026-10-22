"""Re-keying stores from the previous description rule to the current one
(spec §5, decision 5): from v1 to v2 at first, now from v2 to v3, which
gathers fee and tax lines (IOF abroad, broker fees...) into one store each,
merging many keys into one (the picks are recounted from the facts, so they
add up).

The v2 rule of `merchant_key` drops a short acquirer prefix before the "*"
("Mlp*Netshoes" was the store "MLP", now "NETSHOES"). Rows keyed under v1
would otherwise sit apart from the stores the app now computes: a pending
suggestion grouped under "MLP", a memory row "MLP" nobody looks up again.
This module rewrites them once, at deploy, after `alembic upgrade head`
(`scripts/rekey_merchant_keys.py`).

Per workspace, under the workspace lock, and only when no `classify` or
`redistribute` run is busy (those write suggestions and memory; a busy
workspace is skipped and reported with the run that holds it, to be run
again later). Every transaction gets its v1 and v2 key, the payee read
within the workspace as the pipeline reads it. Which keys change is told
by the pipeline's candidates (posted, not a transfer pair, not an opening
balance, not ignored): a key is *dead* when some candidate had it under v1
and none has it under v2.

**Which memory keys are touched.** A memory row `K` that some candidate had
as its v1 key has as destinations the v2 keys of those candidates, plus
`K` itself when it is still some candidate's v2 key. When that is only `K`,
nothing happens. One other destination is a **merge** into it; two or more
a **split** (`K` mixed stores, like `MLP` for Netshoes and Kabum). The
touched keys are those `K` and all their destinations; no other memory row
is ever modified.

**Touched keys are recomputed from the facts**, not by combining counts
(adding or taking the larger of old numbers drifts from what an undo takes
back). For each touched key still in use, over **every** transaction that
now carries it (candidate or not: the live memory learns from any row a
person files, and no stale mark may survive on any of them):

- `counts[category]` = the person's standing decisions for it (R16, one
  pick per decision, not per row). A `user` row's decision is the event
  that caused its current category: its latest standing event (undos and
  undone events aside), passing over category merges (`record_moves`
  re-files a store's rows in one write; the choices were made before). It
  counts only when it is a person's `user` event (`user_id` set): a sync
  inheriting a parcel's category from the first one writes a `user` event
  with no person, which the live memory never counted. Decisions of one
  store, category and person less than `DECISION_GAP` apart are one (one
  bulk write stamps each row as it goes); a `user` row with no event at
  all (older than the events) is a decision of its own. A decision whose
  acceptance settled a suggestion under a key the re-key does not touch
  belongs to that key (its undo takes the pick there): not counted, and
  every event that suggestion settled keeps its mark. A transfer leg whose
  category a cascade copied from the other leg is that leg's decision, not
  one of its own; a row the pipeline does not consider whose untouched v1
  key holds memory already gave that key its pick and is not counted again.
- `memory_pick`: exactly one event of each counted decision carries it
  (one already marked if any, else the earliest); every other marked event
  of those rows loses it (a superseded edit, a clear, a second mark of the
  same decision). An undo then takes back exactly one pick per decision it
  reverts, from the key the row has now, and the memory never holds more
  than the facts: an undo that brings an older decision back does not add
  its pick, so the count may fall short, never exceed.
- the last choice is the category of the latest decision.
- a row whose counts or last choice change, or that received a merge,
  drops its cached AI decision and clean name (they described another
  store). A key with no decision and no row stays absent.

Notes: a merge keeps the newest note of the rows it joins; a split never
copies a note to its destinations (a note goes into the prompt, and one
about the mixed store would mislead the model about every store it held);
a mixed key still in use loses its note when it is rebuilt. Every note
dropped is reported with its text, so nothing the person wrote vanishes
unseen. A dead key's row goes (renamed to its merge target when that has
no row, so the note and id stay).

**Suggestions** (every status: a dismissed one is what keeps a store
"ignored"; any transaction, candidate or not) whose key is the v1 key of
their transaction, a dead key, or a touched key, take the transaction's v2
key, which is the key an undo of an accepted one takes the pick from. A
suggestion under an untouched key (a payee's `P:...`) stays where its pick
is. **Organizer proposals** still pending (`organize`
runs, `create_category` ops not decided) naming a store by a dead key take
the v2 key most of the store's transactions now have, so approving one
later repoints memory that exists (a store that mixed several repoints
the dominant one; its rows all move as before).

Rerunning changes nothing: dead keys are gone; a recompute of the same
facts gives the same counts, last choice and marks, and a row it would not
change is left untouched (so a mixed key still in use whose counts already
equal the facts on the first run keeps its AI cache and note). Nothing here
commits, except `rekey_all`, once per workspace.
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
from app.services.categorization.events import CATEGORY_MERGE_REASON
from app.services.categorization.merchant_key import MERCHANT_KEY_VERSION, merchant_key

#: The rule the stored keys came from and the one they move to.
OLD_VERSION = MERCHANT_KEY_VERSION - 1
NEW_VERSION = MERCHANT_KEY_VERSION
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
    #: Note text the change dropped.
    notes: tuple[str, ...] = ()


@dataclass
class RekeyReport:
    workspace_id: uuid.UUID
    suggestions: int = 0
    merged: int = 0
    split: int = 0
    #: Memory rows recomputed that changed (or were created).
    seeded: int = 0
    skipped: str | None = None
    #: Events that gained / lost `memory_pick`.
    marked: int = 0
    unmarked: int = 0
    #: Stores of pending organizer proposals re-keyed.
    proposals: int = 0
    #: The run that made the workspace busy (kind, status, id, age).
    busy_run: str | None = None
    changes: list[KeyChange] = field(default_factory=list)
    #: Every recomputed row that changed: (key, picks before or None when
    #: created, picks after).
    recounts: list[tuple[str, int | None, int]] = field(default_factory=list)


@dataclass(frozen=True)
class _Owner:
    """The accepted suggestion an undo of the row's decision takes the pick
    back from (`review._take_back`): its key, and when it was settled."""

    key: str
    resolved_at: datetime


@dataclass(frozen=True)
class _Row:
    id: uuid.UUID
    old: str
    new: str
    category_id: uuid.UUID | None
    source: str
    day: date
    candidate: bool
    pair: uuid.UUID | None = None


@dataclass(frozen=True)
class _Event:
    id: uuid.UUID
    at: datetime
    user_id: uuid.UUID | None
    memory_pick: bool
    source: str = "user"
    new_category_id: uuid.UUID | None = None
    reason: str | None = None


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

    def carrier(self) -> _Event | None:
        """The event that carries the decision's pick."""
        marked = [ev for ev in self.events if ev.memory_pick]
        return (marked or self.events or [None])[0]


@dataclass
class _Facts:
    decisions: list[_Decision] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return dict(Counter(d.category for d in self.decisions))

    def last(self) -> uuid.UUID | None:
        if not self.decisions:
            return None
        return uuid.UUID(max(self.decisions, key=lambda d: d.order).category)


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
    """Every transaction with its v1 and v2 stored key (the payee read within
    the workspace, as the pipeline reads it), flagged when it is one of the
    pipeline's candidates."""
    candidate = and_(
        Transaction.status == "posted",
        Transaction.transfer_pair_id.is_(None),
        Transaction.source != "opening_balance",
        Transaction.is_ignored.is_(False),
    )
    result = await session.execute(
        select(
            Transaction.id,
            Transaction.description,
            Payee.name,
            Payee.source,
            Transaction.category_id,
            Transaction.category_source,
            Transaction.date,
            candidate.label("candidate"),
            Transaction.transfer_pair_id,
        )
        .outerjoin(
            Payee, and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id)
        )
        .where(Transaction.workspace_id == workspace_id)
    )
    cache: dict[tuple[str | None, str | None, str | None], tuple[str, str]] = {}
    rows: list[_Row] = []
    for (
        tx_id, description, payee_name, payee_source, category_id, source, day, is_candidate,
        pair,
    ) in result.all():
        names = (description, payee_name, payee_source)
        keys = cache.get(names)
        if keys is None:
            keys = cache[names] = (
                _key(description, payee_name, payee_source, OLD_VERSION),
                _key(description, payee_name, payee_source, NEW_VERSION),
            )
        old, new = keys
        rows.append(_Row(tx_id, old, new, category_id, source, day, bool(is_candidate), pair))
    return rows


async def _rekey_suggestions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    by_tx: dict[uuid.UUID, _Row],
    dead: dict[str, str | None],
    touched: set[str],
) -> tuple[int, dict[uuid.UUID, _Owner]]:
    """Re-key suggestions; returns how many moved and, per transaction, the
    latest accepted suggestion as it now stands.

    A suggestion moves to its row's v2 key when its key is the row's v1 key,
    a dead key, or a key the re-key touches. A key the re-key does not touch
    (a payee's `P:...` set before the row changed store, say) stays: the
    pick its acceptance gave lives there, and its undo takes it from there.
    """
    suggestions = (
        await session.execute(
            select(CategorizationSuggestion)
            .where(CategorizationSuggestion.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalars().all()
    moved = 0
    owners: dict[uuid.UUID, _Owner] = {}
    for sug in suggestions:
        key = sug.merchant_key
        row = by_tx.get(sug.transaction_id)
        target: str | None = None
        if row is not None and (key == row.old or key in dead or key in touched):
            target = row.new
        elif row is None and key in dead:
            target = dead[key]
        if target is not None and target != key:
            sug.merchant_key = target
            moved += 1
        resolved = _utc(sug.resolved_at)
        if sug.status == "accepted" and resolved is not None:
            owner = owners.get(sug.transaction_id)
            if owner is None or resolved > owner.resolved_at:
                owners[sug.transaction_id] = _Owner(sug.merchant_key, resolved)
    return moved, owners


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


def _cause(
    events: list[tuple[_Event, datetime | None]],
) -> tuple[_Event | None, bool]:
    """The event behind a row's current category, and whether a category
    merge re-filed it since: the latest standing one, undos and undone
    events aside, passing over merges."""
    standing = sorted(
        (ev for ev, undone_at in events if undone_at is None and ev.source != "undo"),
        key=lambda ev: (ev.at, ev.id),
    )
    merged = False
    for ev in reversed(standing):
        if ev.reason == CATEGORY_MERGE_REASON:
            merged = True
            continue
        return ev, merged
    return None, merged


def _is_mirror(cause: _Event, twin: _Event | None) -> bool:
    """Whether `cause` is the copy a transfer cascade wrote on the paired leg
    of the person's change on `twin` (same person and category, within
    `DECISION_GAP`). The change is the marked one, else the earlier (the
    cascade is recorded after it), else the lower id."""
    if (
        twin is None
        or twin.source != "user"
        or twin.user_id != cause.user_id
        or twin.new_category_id != cause.new_category_id
        or abs(twin.at - cause.at) > DECISION_GAP
    ):
        return False
    order = min((cause, twin), key=lambda ev: (not ev.memory_pick, ev.at, ev.id))
    return order is twin


async def _facts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[_Row],
    owners: dict[uuid.UUID, _Owner],
    partners: dict[uuid.UUID, uuid.UUID],
    skip: set[uuid.UUID],
) -> dict[str, _Facts]:
    """Per store key, the person's standing decisions behind its `user` rows,
    and the events whose pick belongs to a key the re-key leaves alone.

    A row's decision is the event that caused its current category: its
    latest standing event (undos and undone events aside), passing over
    category merges (`record_moves` re-files a store's rows in one write; the
    choice is still the one made before). It counts when it is a person's
    `user` event (`user_id` set): a sync inheriting a parcel's category from
    the first one writes a `user` event with no person, which the live
    memory never counted, and neither does this. A row with no such event at
    all (filed before the events) is a decision of its own. A decision whose
    acceptance settled a suggestion under another, untouched key is that
    key's (its undo takes the pick there): not counted here. A transfer leg
    whose category a cascade copied from the other leg (`partners`) is that
    leg's decision, not one of its own. Rows in `skip` (not candidates, whose
    untouched v1 key already holds their pick) are not counted again.
    """
    chosen = [
        row for row in rows
        if row.source == "user" and row.category_id is not None and row.id not in skip
    ]
    by_tx: dict[uuid.UUID, list[tuple[_Event, datetime | None]]] = defaultdict(list)
    ids = list(
        dict.fromkeys(
            [row.id for row in chosen]
            + [partners[row.id] for row in chosen if row.id in partners]
        )
    )
    for start in range(0, len(ids), memory.CHUNK):
        found = await session.execute(
            select(
                CategorizationEvent.id,
                CategorizationEvent.transaction_id,
                CategorizationEvent.source,
                CategorizationEvent.user_id,
                CategorizationEvent.new_category_id,
                CategorizationEvent.created_at,
                CategorizationEvent.undone_at,
                CategorizationEvent.reason,
                CategorizationEvent.memory_pick,
            ).where(
                CategorizationEvent.workspace_id == workspace_id,
                CategorizationEvent.transaction_id.in_(ids[start:start + memory.CHUNK]),
            )
        )
        for ev_id, tx_id, source, user_id, category_id, created_at, undone_at, reason, pick in (
            found.all()
        ):
            at = _utc(created_at)
            if at is None:
                continue
            event = _Event(ev_id, at, user_id, bool(pick), source, category_id, reason)
            by_tx[tx_id].append((event, undone_at))

    facts: dict[str, _Facts] = defaultdict(_Facts)
    bursts: dict[tuple[str, str, uuid.UUID | None], list[tuple[_Event, date]]] = defaultdict(list)
    for row in chosen:
        category = str(row.category_id)
        cause, merged = _cause(by_tx.get(row.id, []))
        if cause is None:
            facts[row.new].decisions.append(_Decision(category, [], row.day))
            continue
        if cause.source != "user" or cause.user_id is None:
            continue  # inherited, or not a person's choice: no pick
        if not merged and cause.new_category_id != row.category_id:
            continue
        owner = owners.get(row.id)
        if owner is not None and owner.key != row.new and owner.resolved_at >= cause.at:
            continue
        partner = partners.get(row.id)
        if partner is not None and _is_mirror(cause, _cause(by_tx.get(partner, []))[0]):
            continue
        bursts[(row.new, category, cause.user_id)].append((cause, row.day))
    for (key, category, _user), found_events in bursts.items():
        found_events.sort(key=lambda pair: (pair[0].at, pair[0].id))
        current: _Decision | None = None
        for event, day in found_events:
            if current is None or event.at - current.events[-1].at > DECISION_GAP:
                current = _Decision(category, [], day)
                facts[key].decisions.append(current)
            current.events.append(event)
            current.day = max(current.day, day)
    return dict(facts)


async def _set_picks(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[_Row],
    facts: list[_Facts],
    owners: dict[uuid.UUID, _Owner],
) -> tuple[int, int]:
    """One `memory_pick` per counted decision, none on any other event of
    `rows`, except those whose pick lives under an untouched key: every
    event a row's accepted suggestion under another key settled (created
    up to its `resolved_at`), which `review._take_back` undoes there.
    Returns (marked, unmarked)."""
    new_key = {row.id: row.new for row in rows}
    tx_ids = list(new_key)
    carriers: dict[uuid.UUID, bool] = {}
    for found in facts:
        for decision in found.decisions:
            carrier = decision.carrier()
            if carrier is not None:
                carriers[carrier.id] = carrier.memory_pick
    stale: list[uuid.UUID] = []
    for start in range(0, len(tx_ids), memory.CHUNK):
        for ev_id, tx_id, created_at in (
            await session.execute(
                select(
                    CategorizationEvent.id,
                    CategorizationEvent.transaction_id,
                    CategorizationEvent.created_at,
                ).where(
                    CategorizationEvent.workspace_id == workspace_id,
                    CategorizationEvent.transaction_id.in_(tx_ids[start:start + memory.CHUNK]),
                    CategorizationEvent.memory_pick.is_(True),
                )
            )
        ).all():
            if ev_id in carriers:
                continue
            owner = owners.get(tx_id)
            at = _utc(created_at)
            if (
                owner is not None
                and owner.key != new_key[tx_id]
                and at is not None
                and at <= owner.resolved_at
            ):
                continue  # its undo takes the pick from the owner's key
            stale.append(ev_id)
    mark = [ev_id for ev_id, already in carriers.items() if not already]
    for ids, value in ((mark, True), (stale, False)):
        for start in range(0, len(ids), memory.CHUNK):
            await session.execute(
                sa_update(CategorizationEvent)
                .where(CategorizationEvent.id.in_(ids[start:start + memory.CHUNK]))
                .values(memory_pick=value)
                .execution_options(synchronize_session=False)
            )
    return len(mark), len(stale)


def _latest_note(rows: list[MerchantMemory]) -> tuple[MerchantMemory | None, list[str]]:
    """The row with the newest note, and the other note texts."""
    noted = sorted(
        (r for r in rows if r.note is not None),
        key=lambda r: _utc(r.note_updated_at) or _FLOOR,
        reverse=True,
    )
    if not noted:
        return None, []
    kept = noted[0]
    return kept, [r.note for r in noted[1:] if r.note is not None and r.note != kept.note]


async def _rekey_memory(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[_Row],
    every: list[_Row],
    owners: dict[uuid.UUID, _Owner],
    stored: dict[str, MerchantMemory],
    merges: dict[str, list[str]],
    splits: dict[str, set[str]],
    report: RekeyReport,
) -> None:
    current = {row.new for row in rows}
    touched: set[str] = set(merges) | set(splits)
    for sources in merges.values():
        touched.update(sources)
    for destinations in splits.values():
        touched.update(destinations)
    live = touched & current
    # Every row now carrying a touched key, candidate or not: the live
    # memory learns from any row a person files, and no stale mark may stay.
    facts_rows = [row for row in every if row.new in live]
    by_pair: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for row in every:
        if row.pair is not None:
            by_pair[row.pair].append(row.id)
    partners = {
        tx_id: other
        for legs in by_pair.values() if len(legs) == 2
        for tx_id, other in (legs, legs[::-1])
    }
    # A row the pipeline does not consider, whose v1 key the re-key leaves
    # alone with memory there, already gave that key its pick.
    skip = {
        row.id for row in facts_rows
        if not row.candidate and row.old != row.new
        and row.old in stored and row.old not in touched
    }
    facts = await _facts(session, workspace_id, facts_rows, owners, partners, skip)
    notes: dict[str, list[str]] = defaultdict(list)
    picks = {key: _picks(stored[key]) for key in touched if key in stored}

    # Dead keys first: a merge source joins its target (renamed into it when
    # the target has no row), a split source goes.
    received: set[str] = set()
    for target, sources in sorted(merges.items()):
        joining = [stored.pop(key) for key in sources]
        row = stored.get(target)
        kept, dropped = _latest_note(([row] if row is not None else []) + joining)
        if row is None:
            row = joining.pop(0)
            row.merchant_key = target
            stored[target] = row
        for gone in joining:
            await session.delete(gone)
        await session.flush()
        if kept is not None and kept is not row:
            row.note, row.note_updated_at = kept.note, kept.note_updated_at
        received.add(target)
        for key in sources:
            # Notes dropped by a merge are reported once, on its first source.
            report.changes.append(
                KeyChange(
                    MERGE, key, (target,), picks.get(key, 0),
                    tuple(dropped) if key == sources[0] else (),
                )
            )
            report.merged += 1
    for key in sorted(splits):
        if key not in current:
            row = stored.pop(key)
            if row.note is not None:
                notes[key].append(row.note)
            await session.delete(row)
    await session.flush()

    changed: set[str] = set()
    for key in sorted(live):
        found = facts.get(key) or _Facts()
        counts, last = found.counts(), found.last()
        row = stored.get(key)
        before = _picks(row) if row is not None else None
        if row is None:
            if not counts:
                continue
            stored[key] = MerchantMemory(
                id=uuid.uuid4(), workspace_id=workspace_id, merchant_key=key,
                counts=counts, last_user_category_id=last,
            )
            session.add(stored[key])
        elif (
            key in received
            or counts != memory._counts(row.counts)
            or last != row.last_user_category_id
        ):
            row.counts = counts  # reassigned: in-place JSON mutation is not tracked
            row.last_user_category_id = last
            row.ai_category_id = None
            row.ai_confidence = None
            row.ai_clean_name = None
            if key in splits and row.note is not None:
                # A mixed key still in use: its note described the mix.
                notes[key].append(row.note)
                row.note = None
                row.note_updated_at = None
        else:
            continue
        changed.add(key)
        report.seeded += 1
        report.recounts.append((key, before, sum(counts.values())))
    report.marked, report.unmarked = await _set_picks(
        session, workspace_id, facts_rows,
        [facts[key] for key in sorted(live) if key in facts], owners,
    )

    for key in sorted(splits):
        destinations = splits[key]
        if key in current and not destinations & changed:
            continue  # a key still in use, already rebuilt: nothing to do
        report.split += 1
        report.changes.append(
            KeyChange(
                SPLIT, key, tuple(sorted(destinations)), picks.get(key, 0), tuple(notes[key])
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
        every = await _load_rows(session, workspace_id)
        rows = [row for row in every if row.candidate]
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
        merges: dict[str, list[str]] = defaultdict(list)
        splits: dict[str, set[str]] = {}
        for key in sorted(stored):
            if key not in mapping:
                continue
            destinations = mapping[key] | ({key} & current)
            if destinations == {key}:
                continue
            if len(destinations) == 1:
                merges[next(iter(destinations))].append(key)
            else:
                splits[key] = destinations
        touched = set(splits) | set(merges)
        for sources in merges.values():
            touched.update(sources)
        for destinations in splits.values():
            touched.update(destinations)

        report.suggestions, owners = await _rekey_suggestions(
            session, workspace_id, {row.id: row for row in every}, dead, touched
        )
        report.proposals = await _rekey_proposals(
            session, workspace_id, {row.id: row for row in every}, dead
        )
        await _rekey_memory(
            session, workspace_id, rows, every, owners, stored, dict(merges), splits, report
        )
        await session.flush()
    if dry_run:
        await session.rollback()
    return report


async def _workspace_ids(session_maker: Any, workspace_id: uuid.UUID | None) -> list[uuid.UUID]:
    if workspace_id is not None:
        return [workspace_id]
    async with session_maker() as session:
        return list(
            (await session.execute(select(Workspace.id).order_by(Workspace.id))).scalars()
        )


async def busy_workspaces(
    session_maker: Any, *, workspace_id: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, str]]:
    """The workspaces (all, or only `workspace_id`) with a busy `classify` /
    `redistribute` run, each with the run that holds it. Read-only: the
    script refuses a real run while any is busy, instead of leaving them
    half done; `rekey_workspace` still checks again under the lock."""
    found: list[tuple[uuid.UUID, str]] = []
    ids = await _workspace_ids(session_maker, workspace_id)
    async with session_maker() as session:
        for ws_id in ids:
            busy = await _busy_run(session, ws_id)
            if busy is not None:
                found.append((ws_id, busy))
    return found


async def rekey_all(
    session_maker: Any, *, dry_run: bool = False, workspace_id: uuid.UUID | None = None
) -> list[RekeyReport]:
    """`rekey_workspace` for every workspace (or only `workspace_id`), each in
    its own transaction and commit, so one busy or failing workspace leaves
    the ones before it done."""
    reports: list[RekeyReport] = []
    for ws_id in await _workspace_ids(session_maker, workspace_id):
        async with session_maker() as session:
            report = await rekey_workspace(session, ws_id, dry_run=dry_run)
            if not dry_run:
                await session.commit()
        reports.append(report)
    return reports
