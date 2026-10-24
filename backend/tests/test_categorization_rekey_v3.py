"""Re-keying from v2 to v3 (`rekey`): many fee lines, each under its own
store until now, become one store, and the picks they had add up."""
from datetime import timedelta


from app.services.categorization import rekey
from app.services.categorization.merchant_key import IOF_ABROAD_KEY
from tests.test_categorization_api import _suggest, _tx
from tests.test_categorization_rekey import NOON, _memory, _rows, _user_event, _zero


def test_rekey_goes_from_the_previous_rule_to_the_current():
    assert (rekey.OLD_VERSION, rekey.NEW_VERSION) == (2, 3)


async def test_rekey_gathers_fee_lines_into_one_store_and_adds_their_picks(
    session, test_user, test_account, test_workspace, test_categories
):
    taxes, travel, _ = test_categories
    a, b = str(taxes.id), str(travel.id)
    ws = test_workspace.id
    lines = [
        ("Best Buy Iof", taxes),
        ("Guitar Center IOF", taxes),
        ("Iof de Partners ON Booking BV", travel),
    ]
    txs = []
    for hours, (description, category) in enumerate(lines):
        tx = _tx(session, test_user, test_account, description, source="user", category_id=category.id)
        txs.append(tx)
        await session.flush()
        _user_event(session, tx, category.id, NOON + timedelta(hours=hours))
    _memory(session, ws, "BEST BUY IOF", {a: 1}, last=taxes.id)
    _memory(session, ws, "GUITAR CENTER IOF", {a: 1}, last=taxes.id)
    _memory(session, ws, "IOF DE PARTNERS ON BOOKING BV", {b: 1}, last=travel.id)
    pending = _tx(session, test_user, test_account, "IOF gerado por compra em moeda estrangeira")
    await session.flush()
    suggestion = _suggest(session, pending, "IOF GERADO POR COMPRA EM MOEDA ESTRANGEIRA")
    await session.commit()

    dry = await rekey.rekey_workspace(session, ws, dry_run=True)
    await session.rollback()
    assert dry.merged == 3
    assert set(await _rows(session, ws)) == {
        "BEST BUY IOF", "GUITAR CENTER IOF", "IOF DE PARTNERS ON BOOKING BV",
    }

    report = await rekey.rekey_workspace(session, ws)
    await session.commit()
    rows = await _rows(session, ws)
    assert set(rows) == {IOF_ABROAD_KEY}
    assert rows[IOF_ABROAD_KEY].counts == {a: 2, b: 1}
    assert report.merged == 3
    await session.refresh(suggestion)
    assert suggestion.merchant_key == IOF_ABROAD_KEY

    again = await rekey.rekey_workspace(session, ws)
    await session.commit()
    assert _zero(again)
