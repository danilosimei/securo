"""The store note: the write route, the review queue and the provenance carry it."""
import pytest

from app.models.categorization import MerchantMemory
from tests.test_categorization_api import API, _member, _suggest, _tx

pytestmark = pytest.mark.asyncio


async def _note(session, ws_id, key):
    from sqlalchemy import select

    return (
        await session.execute(
            select(MerchantMemory).where(
                MerchantMemory.workspace_id == ws_id, MerchantMemory.merchant_key == key
            )
        )
    ).scalar_one_or_none()


async def test_put_note_creates_and_clears(client, auth_headers, session, test_workspace):
    resp = await client.put(
        f"{API}/notes", json={"merchant_key": "AURINO", "note": "jardineiro"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merchant_key": "AURINO", "note": "jardineiro"}
    row = await _note(session, test_workspace.id, "AURINO")
    assert row is not None and row.note == "jardineiro" and row.counts == {}

    resp = await client.put(
        f"{API}/notes", json={"merchant_key": "AURINO", "note": None}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merchant_key": "AURINO", "note": None}
    await session.refresh(row)
    assert row.note is None


async def test_put_note_requires_write(client, session, test_workspace):
    headers = await _member(session, client, test_workspace, "viewer", "viewer-note@example.com")
    resp = await client.put(
        f"{API}/notes", json={"merchant_key": "AURINO", "note": "x"}, headers=headers
    )
    assert resp.status_code == 403


async def test_put_note_rejects_too_long_text(client, auth_headers):
    resp = await client.put(
        f"{API}/notes", json={"merchant_key": "AURINO", "note": "x" * 401}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_review_group_carries_note(client, auth_headers, session, test_user, test_account, test_categories):
    tx = _tx(session, test_user, test_account, "PADARIA DO ZE")
    await session.flush()
    _suggest(session, tx, "PADARIA DO ZE", category_id=test_categories[0].id)
    await session.commit()
    await client.put(
        f"{API}/notes", json={"merchant_key": "PADARIA DO ZE", "note": "padaria da esquina"},
        headers=auth_headers,
    )
    body = (await client.get(f"{API}/review", headers=auth_headers)).json()
    assert body["groups"][0]["note"] == "padaria da esquina"


async def test_provenance_carries_store_note(client, auth_headers, session, test_user, test_account):
    from app.services.categorization.merchant_key import merchant_key

    tx = _tx(session, test_user, test_account, "AURINO JARDINAGEM")
    await session.commit()
    resp = await client.get(f"{API}/transactions/{tx.id}/provenance", headers=auth_headers)
    assert resp.json()["store_note"] is None

    key = merchant_key("AURINO JARDINAGEM")
    await client.put(
        f"{API}/notes", json={"merchant_key": key, "note": "jardineiro"}, headers=auth_headers
    )
    resp = await client.get(f"{API}/transactions/{tx.id}/provenance", headers=auth_headers)
    assert resp.json()["store_note"] == {"merchant_key": key, "note": "jardineiro"}
