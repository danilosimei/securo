"""Backfill of `transactions.merchant_key` (service behind the script)."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.models.transaction import Transaction
from app.services.categorization.key_backfill import backfill_all
from tests.conftest import TestSessionLocal
from tests.test_categorization_api import _tx

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_merchant_keys.py"


async def _keys(session):
    session.expire_all()
    rows = await session.execute(select(Transaction.description, Transaction.merchant_key))
    return dict(rows.all())


@pytest.mark.asyncio
async def test_backfill_fills_nulls_fixes_stale_and_is_idempotent(
    session, test_user, test_account, test_workspace
):
    ws_id = test_workspace.id
    test_account.workspace_id = ws_id
    _tx(session, test_user, test_account, "Padaria Estrela")
    _tx(session, test_user, test_account, "Mercado Dia")
    _tx(session, test_user, test_account, "Farmacia Sol")
    await session.commit()
    # Core update: what rows older than the column (or a stale key) look like.
    await session.execute(
        update(Transaction).where(Transaction.description == "Padaria Estrela").values(merchant_key=None)
    )
    await session.execute(
        update(Transaction).where(Transaction.description == "Mercado Dia").values(merchant_key="VELHA")
    )
    await session.commit()

    dry = await backfill_all(TestSessionLocal, dry_run=True, workspace_id=ws_id, batch=2)
    assert (dry[0].scanned, dry[0].filled, dry[0].corrected) == (3, 1, 1)
    assert (await _keys(session))["Padaria Estrela"] is None

    first = await backfill_all(TestSessionLocal, workspace_id=ws_id, batch=2)
    assert (first[0].filled, first[0].corrected) == (1, 1)
    keys = await _keys(session)
    assert keys["Padaria Estrela"] == "PADARIA ESTRELA"
    assert keys["Mercado Dia"] == "MERCADO DIA"
    assert keys["Farmacia Sol"] == "FARMACIA SOL"

    second = await backfill_all(TestSessionLocal, workspace_id=ws_id, batch=2)
    assert second[0].changed == 0


@pytest.mark.asyncio
async def test_backfill_moves_v2_keys_to_the_grouped_v3_store(
    session, test_user, test_account, test_workspace
):
    ws_id = test_workspace.id
    test_account.workspace_id = ws_id
    _tx(session, test_user, test_account, "Best Buy Iof")
    _tx(session, test_user, test_account, "Guitar Center IOF")
    await session.commit()
    # What the rows carry after the v2 backfill.
    for description, old in (("Best Buy Iof", "BEST BUY IOF"), ("Guitar Center IOF", "GUITAR CENTER IOF")):
        await session.execute(
            update(Transaction).where(Transaction.description == description).values(merchant_key=old)
        )
    await session.commit()

    dry = await backfill_all(TestSessionLocal, dry_run=True, workspace_id=ws_id)
    assert dry[0].corrected == 2
    assert (await _keys(session))["Best Buy Iof"] == "BEST BUY IOF"
    await backfill_all(TestSessionLocal, workspace_id=ws_id)
    assert set((await _keys(session)).values()) == {"IOF COMPRAS NO EXTERIOR"}


@pytest.mark.asyncio
async def test_script_dry_run_prints_totals(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("backfill_script", SCRIPT)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    seen = {}

    async def fake_all(session_maker, *, dry_run=False, workspace_id=None):
        seen["dry_run"] = dry_run
        return []

    async def _noop():
        return None

    monkeypatch.setattr(script, "backfill_all", fake_all)
    monkeypatch.setattr(script, "_session_maker", lambda: (None, _noop))
    assert await script.run(["--dry-run"]) == 0
    assert seen["dry_run"] is True
    assert "nada foi gravado" in capsys.readouterr().out
