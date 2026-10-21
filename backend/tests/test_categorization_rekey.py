"""Re-keying stores from the v1 description rule to v2 (`rekey`)."""
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.services.categorization import memory, rekey
from app.services.categorization.merchant_key import merchant_key
from tests.test_categorization_api import _suggest, _tx
from tests.test_merchant_key import REAL_PREFIX_CASES

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rekey_merchant_keys.py"
NOON = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


def _memory(session, ws_id, key, counts, *, note=None, note_at=None, updated_at=None,
            last=None, ai=None):
    row = MerchantMemory(
        id=uuid.uuid4(), workspace_id=ws_id, merchant_key=key, counts=counts,
        last_user_category_id=last, note=note, note_updated_at=note_at,
        ai_category_id=ai, ai_confidence=90 if ai else None,
        ai_clean_name=f"{key.title()} limpo" if ai else None,
    )
    if updated_at is not None:
        row.updated_at = updated_at
    session.add(row)
    return row


def _user_event(session, tx, category_id, at, *, user_id=None, pick=False):
    ev = CategorizationEvent(
        id=uuid.uuid4(), workspace_id=tx.workspace_id, transaction_id=tx.id,
        source="user", previous_source="none", new_category_id=category_id, created_at=at,
        user_id=user_id, memory_pick=pick,
    )
    session.add(ev)
    return ev


async def _rows(session, ws_id) -> dict[str, MerchantMemory]:
    rows = (
        await session.execute(select(MerchantMemory).where(MerchantMemory.workspace_id == ws_id))
    ).scalars().all()
    for row in rows:
        await session.refresh(row)
    return {row.merchant_key: row for row in rows}


async def _snapshot(session, ws_id):
    rows = await _rows(session, ws_id)
    suggestions = (
        await session.execute(
            select(CategorizationSuggestion.id, CategorizationSuggestion.merchant_key,
                   CategorizationSuggestion.status)
            .where(CategorizationSuggestion.workspace_id == ws_id)
        )
    ).all()
    return (
        {
            key: (row.id, row.counts, row.last_user_category_id, row.ai_category_id,
                  row.ai_confidence, row.ai_clean_name, row.note, row.note_updated_at,
                  row.updated_at)
            for key, row in rows.items()
        },
        sorted(suggestions),
    )


def _zero(report: rekey.RekeyReport) -> bool:
    return (report.suggestions, report.merged, report.split, report.seeded,
            report.skipped, report.changes) == (0, 0, 0, 0, None, [])


async def test_rekey_pending_suggestions(session, test_user, test_account, test_workspace):
    netshoes = _tx(session, test_user, test_account, "Mlp*Netshoes")
    kabum = _tx(session, test_user, test_account, "Mlp*Kabum")
    await session.flush()
    s1 = _suggest(session, netshoes, "MLP")
    s2 = _suggest(session, kabum, "MLP")
    s2.status = "dismissed"
    await session.commit()

    report = await rekey.rekey_workspace(session, test_workspace.id)
    await session.commit()

    await session.refresh(s1)
    await session.refresh(s2)
    assert (s1.merchant_key, s1.status) == ("NETSHOES", "pending")
    assert (s2.merchant_key, s2.status) == ("KABUM", "dismissed")
    assert report.suggestions == 2


async def test_rekey_leaves_suggestions_keyed_by_something_else(
    session, test_user, test_account, test_workspace
):
    """Only keys the old rule produced move: a suggestion keyed otherwise
    (a payee set later, say) was not the rule's doing."""
    tx = _tx(session, test_user, test_account, "Mlp*Netshoes")
    await session.flush()
    sug = _suggest(session, tx, "P:NETSHOES OFICIAL")
    await session.commit()

    report = await rekey.rekey_workspace(session, test_workspace.id)
    await session.commit()

    await session.refresh(sug)
    assert sug.merchant_key == "P:NETSHOES OFICIAL"
    assert report.suggestions == 0


async def test_rekey_merges_converging_rows(
    session, test_user, test_account, test_workspace, test_categories
):
    a, b = (str(c.id) for c in test_categories[:2])
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Ebn *Spotify")
    _memory(session, ws, "EBN", {a: 2}, note="nota antiga", note_at=NOON - timedelta(days=3),
            updated_at=NOON - timedelta(days=2), last=test_categories[0].id,
            ai=test_categories[2].id)
    _memory(session, ws, "SPOTIFY", {a: 1, b: 1}, note="nota nova", note_at=NOON,
            updated_at=NOON - timedelta(days=1), last=test_categories[1].id,
            ai=test_categories[2].id)
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()

    rows = await _rows(session, ws)
    assert set(rows) == {"SPOTIFY"}
    spotify = rows["SPOTIFY"]
    assert spotify.counts == {a: 3, b: 1}
    assert spotify.note == "nota nova"
    assert spotify.last_user_category_id == test_categories[1].id
    assert spotify.ai_category_id is None and spotify.ai_confidence is None
    assert spotify.ai_clean_name == "Spotify limpo"
    assert (report.merged, report.split) == (1, 0)
    assert report.changes == [rekey.KeyChange("merge", "EBN", ("SPOTIFY",), 2, ("nota antiga",))]


async def test_rekey_merge_into_missing_row_moves_it(
    session, test_user, test_account, test_workspace, test_categories
):
    a = str(test_categories[0].id)
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Ebn *Spotify")
    old = _memory(session, ws, "EBN", {a: 2}, note="assinatura", note_at=NOON,
                  ai=test_categories[2].id)
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    rows = await _rows(session, ws)
    assert set(rows) == {"SPOTIFY"}
    assert rows["SPOTIFY"].id == old.id
    assert rows["SPOTIFY"].counts == {a: 2}
    assert rows["SPOTIFY"].note == "assinatura"
    assert rows["SPOTIFY"].ai_category_id is None
    assert rows["SPOTIFY"].ai_clean_name == "Ebn limpo"


async def test_rekey_splits_mixed_prefix_and_reseeds_from_user_rows(
    session, test_user, test_account, test_workspace, test_categories
):
    cat_a, cat_b, cat_c = test_categories
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="ai", category_id=cat_c.id)
    _memory(session, ws, "MLP", {str(cat_a.id): 5}, note="mistura", ai=cat_a.id)
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()

    rows = await _rows(session, ws)
    assert set(rows) == {"NETSHOES", "KABUM"}
    assert rows["NETSHOES"].counts == {str(cat_a.id): 2}
    assert rows["NETSHOES"].last_user_category_id == cat_a.id
    assert rows["KABUM"].counts == {str(cat_b.id): 1}
    assert rows["KABUM"].last_user_category_id == cat_b.id
    assert all(r.ai_category_id is None and r.note is None for r in rows.values())
    assert (report.merged, report.split, report.seeded) == (0, 1, 2)
    assert report.changes == [rekey.KeyChange("split", "MLP", ("KABUM", "NETSHOES"), 5, ("mistura",))]


async def test_rekey_reseed_counts_one_pick_per_decision(
    session, test_user, test_account, test_workspace, test_categories
):
    """R16: rows of a store the person decided together (one bulk, events in
    the same second) are one pick; a later decision is another."""
    cat_a, cat_b, _ = test_categories
    ws = test_workspace.id
    rows = [
        _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
        for _ in range(3)
    ]
    kabum = _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    await session.flush()
    _user_event(session, rows[0], cat_a.id, NOON)
    _user_event(session, rows[1], cat_a.id, NOON + timedelta(milliseconds=400))
    _user_event(session, rows[2], cat_a.id, NOON + timedelta(hours=1))
    # The same bulk filed Kabum too: one pick for that store as well.
    _user_event(session, kabum, cat_b.id, NOON)
    # An undone decision and one for another category do not count.
    gone = _user_event(session, rows[2], cat_b.id, NOON + timedelta(hours=2))
    gone.undone_at = NOON + timedelta(hours=3)
    _memory(session, ws, "MLP", {str(cat_a.id): 9})
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    memory_rows = await _rows(session, ws)
    assert memory_rows["NETSHOES"].counts == {str(cat_a.id): 2}
    assert memory_rows["KABUM"].counts == {str(cat_b.id): 1}


async def test_rekey_reseed_last_choice_is_the_latest_decision(
    session, test_user, test_account, test_workspace, test_categories
):
    """The last choice is the latest decision; a row with no event (filed
    before events existed) is older than any that has one."""
    cat_a, cat_b, cat_c = test_categories
    ws = test_workspace.id
    newest = _tx(session, test_user, test_account, "Mlp*Netshoes", source="user",
                 category_id=cat_a.id, day=NOON.date() - timedelta(days=30))
    older = _tx(session, test_user, test_account, "Mlp*Netshoes", source="user",
                category_id=cat_b.id, day=NOON.date())
    _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_c.id,
        day=NOON.date() + timedelta(days=1))
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    await session.flush()
    _user_event(session, newest, cat_a.id, NOON + timedelta(hours=1))
    _user_event(session, older, cat_b.id, NOON)
    _memory(session, ws, "MLP", {str(cat_a.id): 1})
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    rows = await _rows(session, ws)
    assert rows["NETSHOES"].last_user_category_id == cat_a.id
    assert rows["NETSHOES"].counts == {str(cat_a.id): 1, str(cat_b.id): 1, str(cat_c.id): 1}


async def test_rekey_split_sums_into_existing_row(
    session, test_user, test_account, test_workspace, test_categories
):
    """A destination the memory already knew keeps what it had and gains the
    person's decisions of the rows that come to it."""
    cat_a, cat_b, cat_c = test_categories
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    _memory(session, ws, "NETSHOES", {str(cat_a.id): 1, str(cat_c.id): 1}, note="tenis",
            last=cat_c.id)
    _memory(session, ws, "MLP", {str(cat_a.id): 3})
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    rows = await _rows(session, ws)
    assert rows["NETSHOES"].counts == {str(cat_a.id): 2, str(cat_c.id): 1}
    assert rows["NETSHOES"].note == "tenis"
    assert rows["KABUM"].counts == {str(cat_b.id): 1}


async def test_rekey_is_idempotent(
    session, test_user, test_account, test_workspace, test_categories
):
    cat_a, cat_b, _ = test_categories
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Ebn *Spotify")
    _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    # "Mlp*" alone is still the store MLP: its row is a destination too.
    _tx(session, test_user, test_account, "Mlp*", source="user", category_id=cat_b.id)
    sug_tx = _tx(session, test_user, test_account, "Mlp*Kabum")
    await session.flush()
    _suggest(session, sug_tx, "MLP")
    _memory(session, ws, "EBN", {str(cat_a.id): 2})
    _memory(session, ws, "SPOTIFY", {str(cat_b.id): 1})
    _memory(session, ws, "MLP", {str(cat_a.id): 4}, note="varias lojas")
    await session.commit()

    first = await rekey.rekey_workspace(session, ws)
    await session.commit()
    assert (first.suggestions, first.merged, first.split) == (1, 1, 1)
    rows = await _rows(session, ws)
    assert rows["MLP"].counts == {str(cat_b.id): 1}
    assert rows["MLP"].note is None  # it described the mixed store
    split = [c for c in first.changes if c.kind == "split"]
    assert split == [rekey.KeyChange("split", "MLP", ("KABUM", "MLP", "NETSHOES"), 4, ("varias lojas",))]
    before = await _snapshot(session, ws)

    second = await rekey.rekey_workspace(session, ws)
    await session.commit()

    assert _zero(second)
    assert await _snapshot(session, ws) == before


async def test_rekey_dry_run_changes_nothing(
    session, test_user, test_account, test_workspace, test_categories
):
    cat_a, cat_b, _ = test_categories
    ws = test_workspace.id
    tx = _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    _tx(session, test_user, test_account, "Ebn *Spotify")
    await session.flush()
    _suggest(session, tx, "MLP")
    _memory(session, ws, "MLP", {str(cat_a.id): 5})
    _memory(session, ws, "EBN", {str(cat_a.id): 1})
    await session.commit()
    before = await _snapshot(session, ws)

    report = await rekey.rekey_workspace(session, ws, dry_run=True)

    assert (report.suggestions, report.merged, report.split, report.seeded) == (1, 1, 1, 2)
    assert await _snapshot(session, ws) == before


async def test_rekey_skips_busy_workspace(
    session, test_user, test_account, test_workspace, test_categories
):
    ws = test_workspace.id
    tx = _tx(session, test_user, test_account, "Mlp*Netshoes")
    _tx(session, test_user, test_account, "Ebn *Spotify")
    await session.flush()
    _suggest(session, tx, "MLP")
    _memory(session, ws, "EBN", {str(test_categories[0].id): 1})
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=ws, kind="classify", trigger="ingest",
        status="processing_batch", created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    session.add(run)
    await session.commit()
    before = await _snapshot(session, ws)

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()

    assert report.skipped == "busy"
    assert report.busy_run is not None
    assert report.busy_run.startswith(f"classify processing_batch {run.id}, criada há 3h")
    assert (report.suggestions, report.merged, report.split, report.seeded) == (0, 0, 0, 0)
    assert await _snapshot(session, ws) == before


async def test_rekey_runs_alongside_an_organize_run(
    session, test_user, test_account, test_workspace
):
    """An `organize` run proposes a tree and touches no rows: no reason to wait."""
    ws = test_workspace.id
    tx = _tx(session, test_user, test_account, "Mlp*Netshoes")
    await session.flush()
    _suggest(session, tx, "MLP")
    session.add(CategorizationRun(
        id=uuid.uuid4(), workspace_id=ws, kind="organize", trigger="manual", status="running",
    ))
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)

    assert report.skipped is None and report.suggestions == 1


async def test_rekey_real_prefixes(session, test_user, test_account, test_workspace):
    pairs = []
    for raw, expected in REAL_PREFIX_CASES:
        tx = _tx(session, test_user, test_account, raw)
        pairs.append((tx, expected))
    await session.flush()
    suggestions = [
        (_suggest(session, tx, memory.stored_key(merchant_key(tx.description, version=1))), want)
        for tx, want in pairs
    ]
    await session.commit()

    await rekey.rekey_workspace(session, test_workspace.id)
    await session.commit()

    for sug, expected in suggestions:
        await session.refresh(sug)
    assert [s.merchant_key for s, _ in suggestions] == [want for _, want in suggestions]


async def test_undo_after_rekey_takes_the_pick_from_the_new_key(
    session, test_user, test_account, test_workspace, test_categories
):
    from app.services.categorization import review

    cat_a, cat_b, _ = test_categories
    ws = test_workspace.id
    accepted = _tx(session, test_user, test_account, "Mlp*Netshoes")
    _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    await session.flush()
    sug = _suggest(session, accepted, "MLP")
    await session.commit()
    applied = await review.accept(
        session, ws, test_user.id, merchant_key="MLP", category_id=cat_a.id
    )
    await session.commit()
    assert (await _rows(session, ws))["MLP"].counts == {str(cat_a.id): 1}

    await rekey.rekey_workspace(session, ws)
    await session.commit()
    rows = await _rows(session, ws)
    assert "MLP" not in rows
    assert rows["NETSHOES"].counts == {str(cat_a.id): 2}

    await review.undo(session, ws, event_ids=applied.event_ids, user_id=test_user.id)
    await session.commit()

    rows = await _rows(session, ws)
    assert rows["NETSHOES"].counts == {str(cat_a.id): 1}
    assert rows["KABUM"].counts == {str(cat_b.id): 1}
    await session.refresh(sug)
    assert (sug.merchant_key, sug.status) == ("NETSHOES", "pending")


async def test_rekey_all_postgres(postgres_sessions):
    from decimal import Decimal

    from app.models.account import Account
    from app.models.category import Category
    from app.models.transaction import Transaction
    from tests.test_admin_service import _make_user

    async with postgres_sessions() as session:
        user = await _make_user(session, f"rekey_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.flush()
        ws = account.workspace_id
        cats = [Category(id=uuid.uuid4(), user_id=user.id, name=n) for n in ("A", "B")]
        session.add_all(cats)
        await session.flush()

        def tx(description, category=None):
            row = Transaction(
                id=uuid.uuid4(), user_id=user.id, workspace_id=ws, account_id=account.id,
                description=description, amount=Decimal("10"), currency="BRL",
                date=NOON.date(), effective_date=NOON.date(), type="debit", source="sync",
                status="posted", category_id=category.id if category else None,
                category_source="user" if category else "none",
            )
            session.add(row)
            return row

        spotify = tx("Ebn *Spotify")
        tx("Spotify")
        tx("Mlp*Netshoes", cats[0])
        tx("Mlp*Kabum", cats[1])
        await session.flush()
        _suggest(session, spotify, "EBN")
        _memory(session, ws, "EBN", {str(cats[0].id): 2})
        _memory(session, ws, "SPOTIFY", {str(cats[0].id): 1})
        _memory(session, ws, "MLP", {str(cats[0].id): 3})
        await session.commit()

    reports = await rekey.rekey_all(postgres_sessions, workspace_id=ws)
    assert [(r.suggestions, r.merged, r.split, r.seeded) for r in reports] == [(1, 1, 1, 2)]

    async with postgres_sessions() as session:
        rows = {
            r.merchant_key: r.counts
            for r in (
                await session.execute(
                    select(MerchantMemory).where(MerchantMemory.workspace_id == ws)
                )
            ).scalars()
        }
    assert rows == {
        "SPOTIFY": {str(cats[0].id): 3},
        "NETSHOES": {str(cats[0].id): 1},
        "KABUM": {str(cats[1].id): 1},
    }

    again = await rekey.rekey_all(postgres_sessions, workspace_id=ws)
    assert all(_zero(r) for r in again)


def _load_script():
    spec = importlib.util.spec_from_file_location(f"rekey_script_{uuid.uuid4().hex}", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_rekey_script_parses_args(monkeypatch, capsys):
    script = _load_script()
    calls = []
    ws = uuid.uuid4()

    async def fake_rekey_all(session_maker, *, dry_run=False, workspace_id=None):
        calls.append((dry_run, workspace_id))
        changes = [
            rekey.KeyChange("merge", f"K{i:02d}", ("N",), i) for i in range(25)
        ] + [rekey.KeyChange("split", "MLP", ("KABUM", "NETSHOES"), 99, ("loja do Rafa",))]
        return [
            rekey.RekeyReport(ws, suggestions=12, merged=25, split=1, seeded=2, changes=changes),
            rekey.RekeyReport(uuid.uuid4(), skipped="busy", busy_run="classify running abc, criada há 0h05m"),
        ]

    monkeypatch.setattr(script, "rekey_all", fake_rekey_all)
    monkeypatch.setattr(script, "_session_maker", lambda: (None, _noop))

    assert await script.run(["--dry-run"]) == 0
    assert await script.run(["--workspace", str(ws)]) == 0
    assert calls == [(True, None), (False, ws)]

    out = capsys.readouterr().out
    assert f"{ws} sugestões=12 fundidas=25 divididas=1 semeadas=2" in out
    assert "[pulado: busy (classify running abc, criada há 0h05m)]" in out
    assert "dividir MLP (99 escolhas) → KABUM, NETSHOES" in out
    assert "nota descartada de MLP: 'loja do Rafa'" in out
    assert "fundir  K24 (24 escolhas) → N" in out
    assert "K05" not in out  # top 20 only
    assert "e mais 6" in out
    assert "simulação" in out


async def _noop():
    return None


def test_rekey_script_rejects_bad_workspace():
    script = _load_script()
    with pytest.raises(SystemExit):
        script.parse_args(["--workspace", "nao-e-uuid"])


async def test_undo_of_a_mixed_bulk_after_rekey_leaves_no_phantom_pick(
    session, test_user, test_account, test_workspace, test_categories
):
    """One accept of `MLP` filed Netshoes and Kabum and marked one event. The
    split counts one pick in each store, so each gets a marked event: undoing
    the accept takes both back."""
    from app.services.categorization import review

    cat_a = test_categories[0]
    ws = test_workspace.id
    netshoes = _tx(session, test_user, test_account, "Mlp*Netshoes")
    kabum = _tx(session, test_user, test_account, "Mlp*Kabum")
    await session.flush()
    s1 = _suggest(session, netshoes, "MLP")
    s2 = _suggest(session, kabum, "MLP")
    await session.commit()
    applied = await review.accept(
        session, ws, test_user.id, merchant_key="MLP", category_id=cat_a.id
    )
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()
    rows = await _rows(session, ws)
    assert rows["NETSHOES"].counts == {str(cat_a.id): 1}
    assert rows["KABUM"].counts == {str(cat_a.id): 1}
    assert report.marked == 1
    picks = (
        await session.execute(
            select(CategorizationEvent.memory_pick).where(
                CategorizationEvent.id.in_(applied.event_ids)
            )
        )
    ).scalars().all()
    assert sorted(picks) == [True, True]

    await review.undo(session, ws, event_ids=applied.event_ids, user_id=test_user.id)
    await session.commit()

    rows = await _rows(session, ws)
    assert rows["NETSHOES"].counts == {}
    assert rows["KABUM"].counts == {}
    for sug, key in ((s1, "NETSHOES"), (s2, "KABUM")):
        await session.refresh(sug)
        assert (sug.merchant_key, sug.status) == (key, "pending")
    again = await rekey.rekey_workspace(session, ws)
    assert again.marked == 0


async def test_rekey_bulk_across_a_second_boundary_is_one_decision(
    session, test_user, test_account, test_workspace, test_categories
):
    cat_a, cat_b, _ = test_categories
    ws = test_workspace.id
    one, two, three, other = (
        _tx(session, test_user, test_account, "Mlp*Netshoes", source="user", category_id=cat_a.id)
        for _ in range(4)
    )
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_b.id)
    await session.flush()
    edge = NOON + timedelta(milliseconds=999)
    _user_event(session, one, cat_a.id, edge, user_id=test_user.id)
    _user_event(session, two, cat_a.id, edge + timedelta(milliseconds=2), user_id=test_user.id)
    # The bulk goes on, each row a moment after the previous one.
    _user_event(session, three, cat_a.id, edge + timedelta(seconds=1.5), user_id=test_user.id)
    # Someone else deciding at the same moment is a decision of their own.
    _user_event(session, other, cat_a.id, edge, user_id=None)
    _memory(session, ws, "MLP", {str(cat_a.id): 1})
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    assert (await _rows(session, ws))["NETSHOES"].counts == {str(cat_a.id): 2}


async def test_rekey_mixed_key_still_in_use_loses_its_ai_cache_and_note(
    session, test_user, test_account, test_workspace, test_categories
):
    cat_a, cat_b, cat_c = test_categories
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Mlp*", source="user", category_id=cat_b.id)
    _tx(session, test_user, test_account, "Mlp*Kabum", source="user", category_id=cat_a.id)
    _memory(session, ws, "MLP", {str(cat_b.id): 3}, last=cat_b.id, ai=cat_c.id,
            note="kabum e netshoes", note_at=NOON)
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()

    mlp = (await _rows(session, ws))["MLP"]
    assert mlp.counts == {str(cat_b.id): 1}
    assert (mlp.ai_category_id, mlp.ai_confidence, mlp.ai_clean_name) == (None, None, None)
    assert (mlp.note, mlp.note_updated_at) == (None, None)
    assert report.changes == [
        rekey.KeyChange("split", "MLP", ("KABUM", "MLP"), 3, ("kabum e netshoes",))
    ]


async def test_rekey_merge_last_choice_is_the_latest_decision(
    session, test_user, test_account, test_workspace, test_categories
):
    """`updated_at` also moves when the AI cache is written: the person's
    latest decision on the store's rows says what they chose last."""
    cat_a, cat_b, _ = test_categories
    ws = test_workspace.id
    ebn = _tx(session, test_user, test_account, "Ebn *Spotify", source="user", category_id=cat_a.id)
    plain = _tx(session, test_user, test_account, "Spotify", source="user", category_id=cat_b.id)
    await session.flush()
    _user_event(session, ebn, cat_a.id, NOON + timedelta(hours=2))
    _user_event(session, plain, cat_b.id, NOON)
    _memory(session, ws, "EBN", {str(cat_a.id): 1}, last=cat_a.id, updated_at=NOON)
    _memory(session, ws, "SPOTIFY", {str(cat_b.id): 1}, last=cat_b.id,
            updated_at=NOON + timedelta(days=1))
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    assert (await _rows(session, ws))["SPOTIFY"].last_user_category_id == cat_a.id


async def test_rekey_suggestion_of_a_merged_key_follows_the_merge(
    session, test_user, test_account, test_workspace, test_categories
):
    """A suggestion keyed `EBN` whose own row never was `EBN` under v1 (a
    payee since, say): `EBN` merges into `SPOTIFY`, so the pick an undo of
    it takes back now lives there."""
    ws = test_workspace.id
    _tx(session, test_user, test_account, "Ebn *Spotify")
    plain = _tx(session, test_user, test_account, "Spotify")
    await session.flush()
    sug = _suggest(session, plain, "EBN")
    sug.status = "accepted"
    _memory(session, ws, "EBN", {str(test_categories[0].id): 1})
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()

    await session.refresh(sug)
    assert sug.merchant_key == "SPOTIFY"
    assert report.suggestions == 1
    assert (await rekey.rekey_workspace(session, ws)).suggestions == 0


async def test_rekey_pending_organize_proposal_stores(
    session, test_user, test_account, test_workspace
):
    """A store an undecided proposal names by a dead key takes the key most
    of its rows now have, so approving it repoints memory that exists."""
    ws = test_workspace.id
    n1 = _tx(session, test_user, test_account, "Mlp*Netshoes")
    n2 = _tx(session, test_user, test_account, "Mlp*Netshoes")
    k1 = _tx(session, test_user, test_account, "Mlp*Kabum")
    padaria = _tx(session, test_user, test_account, "Padaria Real")
    await session.flush()
    tx_ids = [str(n1.id), str(k1.id), str(n2.id)]

    def proposal(status):
        return {
            "ops": [{"op": "create_category", "status": status, "children": ["i1", "i2"]}],
            "stores": {
                "i1": {"key": "MLP", "tx": tx_ids},
                "i2": {"key": "PADARIA REAL", "tx": [str(padaria.id)]},
            },
        }

    pending = CategorizationRun(
        id=uuid.uuid4(), workspace_id=ws, kind="organize", trigger="manual", status="done",
        detail=proposal("pending"),
    )
    decided = CategorizationRun(
        id=uuid.uuid4(), workspace_id=ws, kind="organize", trigger="manual", status="done",
        detail=proposal("applied"),
    )
    session.add_all([pending, decided])
    await session.commit()

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()

    await session.refresh(pending)
    await session.refresh(decided)
    assert report.proposals == 1
    assert pending.detail["stores"]["i1"] == {"key": "NETSHOES", "tx": tx_ids}
    assert pending.detail["stores"]["i2"]["key"] == "PADARIA REAL"
    assert decided.detail["stores"]["i1"]["key"] == "MLP"
    assert (await rekey.rekey_workspace(session, ws)).proposals == 0


async def test_rekey_reads_only_candidate_rows_and_workspace_payees(
    session, test_user, test_account, test_workspace, test_categories
):
    """Like the pipeline: an ignored row does not make a store, and a payee
    of another workspace is not read."""
    from app.models.payee import Payee
    from app.models.workspace import Workspace

    ws = test_workspace.id
    elsewhere = Workspace(id=uuid.uuid4(), name="Outro", created_by_user_id=test_user.id)
    session.add(elsewhere)
    await session.flush()
    foreign = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=elsewhere.id,
                    name="Loja Estranha", source="manual")
    session.add(foreign)
    await session.flush()
    _tx(session, test_user, test_account, "Ebn *Spotify", is_ignored=True)
    _tx(session, test_user, test_account, "Mlp*Netshoes", payee_id=foreign.id)
    _memory(session, ws, "EBN", {str(test_categories[0].id): 1})
    mlp = _memory(session, ws, "MLP", {str(test_categories[0].id): 1})
    await session.commit()

    await rekey.rekey_workspace(session, ws)
    await session.commit()

    rows = await _rows(session, ws)
    assert set(rows) == {"EBN", "NETSHOES"}
    assert rows["NETSHOES"].id == mlp.id
