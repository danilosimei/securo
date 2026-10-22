"""GET /api/reports/recurring: fixed costs detected from the store history."""
import uuid
from datetime import date

import pytest

from app.models.category import Category
from tests.test_categorization_api import _tx

URL = "/api/reports/recurring"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr("app.api.reports_breakdown.app_today", lambda: date(2026, 10, 21))


@pytest.fixture
async def world(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    lazer = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Lazer"
    )
    session.add(lazer)
    await session.flush()

    def monthly(desc, amounts, *, first=(2026, 5), day=5, category_id=lazer.id, **extra):
        """One debit per month from `first`, one entry of `amounts` each."""
        year, month = first
        for amount in amounts:
            _tx(
                session, test_user, test_account, desc,
                category_id=category_id, amount=str(amount),
                day=date(year, month, day), **extra,
            )
            month += 1
            if month == 13:
                year, month = year + 1, 1

    return {
        "session": session, "monthly": monthly, "lazer": lazer,
        "user": test_user, "account": test_account,
    }


async def _rows(client, auth_headers):
    response = await client.get(URL, params={"period": "last_12m"}, headers=auth_headers)
    assert response.status_code == 200, response.text
    return {row["merchant_key"]: row for row in response.json()}


@pytest.mark.asyncio
async def test_stable_subscription_is_steady(client, auth_headers, world):
    world["monthly"]("Netflix", ["55.90"] * 6)  # May..Oct
    await world["session"].commit()
    row = (await _rows(client, auth_headers))["NETFLIX"]
    assert row["status"] == "steady"
    assert row["amount_month"] == "55.90"
    assert row["amount_year"] == "670.80"
    assert row["months_seen"] == 6
    assert row["last_date"] == "2026-10-05"
    assert row["category"] == "Lazer"
    assert row["category_id"] == str(world["lazer"].id)
    assert row["label"] == "Netflix"


@pytest.mark.asyncio
async def test_uncategorized_store_has_no_category_id(client, auth_headers, world):
    world["monthly"]("Academia", [120] * 6, category_id=None)
    await world["session"].commit()
    row = (await _rows(client, auth_headers))["ACADEMIA"]
    assert row["category"] == "Sem categoria"
    assert row["category_id"] is None


@pytest.mark.asyncio
async def test_price_increase_is_up(client, auth_headers, world):
    world["monthly"]("Spotify", [20, 20, 20, 20, 20, 25])
    await world["session"].commit()
    assert (await _rows(client, auth_headers))["SPOTIFY"]["status"] == "up"


@pytest.mark.asyncio
async def test_missing_last_two_months_is_stopped(client, auth_headers, world):
    world["monthly"]("Academia", [90, 90, 90, 90], first=(2026, 4))  # Apr..Jul
    await world["session"].commit()
    row = (await _rows(client, auth_headers))["ACADEMIA"]
    assert row["status"] == "stopped"
    assert row["last_date"] == "2026-07-05"


@pytest.mark.asyncio
async def test_two_recent_months_is_new(client, auth_headers, world):
    world["monthly"]("Disney", ["34.90", "34.90"], first=(2026, 9))
    await world["session"].commit()
    assert (await _rows(client, auth_headers))["DISNEY"]["status"] == "new"


@pytest.mark.asyncio
async def test_sporadic_purchases_do_not_count(client, auth_headers, world):
    for month, amount in ((5, 40), (7, 300), (9, 15)):
        _tx(
            world["session"], world["user"], world["account"], "Loja Avulsa",
            category_id=world["lazer"].id, amount=str(amount), day=date(2026, month, 3),
        )
    await world["session"].commit()
    assert "LOJA AVULSA" not in await _rows(client, auth_headers)


@pytest.mark.asyncio
async def test_installments_are_not_recurring(client, auth_headers, world):
    world["monthly"]("Geladeira", [400] * 5, total_installments=12)
    await world["session"].commit()
    assert "GELADEIRA" not in await _rows(client, auth_headers)


@pytest.mark.asyncio
async def test_ordered_by_year_desc(client, auth_headers, world):
    world["monthly"]("Netflix", ["55.90"] * 6)
    world["monthly"]("Condominio", [900] * 6)
    await world["session"].commit()
    response = await client.get(URL, params={"period": "last_12m"}, headers=auth_headers)
    assert [row["merchant_key"] for row in response.json()] == ["CONDOMINIO", "NETFLIX"]
