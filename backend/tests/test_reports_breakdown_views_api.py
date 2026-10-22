"""GET /api/reports/breakdown, the extra ways to look at the spending: every
store at once, by account, narrowed to one account, and against a period
of choice."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from tests.test_categorization_api import _tx
from tests.test_reports_breakdown_levels_api import (  # noqa: F401  (fixtures)
    SEP,
    URL,
    _fixed_today,
    world,
)


async def _get(client, auth_headers, **params):
    response = await client.get(URL, params={**SEP, **params}, headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def card(session, test_user, test_workspace, world):  # noqa: F811
    """A second account, a credit card, with two purchases in September."""
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Cartão Roxo", display_name="Nubank", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    _tx(session, test_user, account, "Enel", category_id=world["luz"].id,
        amount="60.00", day=date(2026, 9, 21))
    _tx(session, test_user, account, "Carrefour", category_id=world["mercado"].id,
        amount="40.00", day=date(2026, 9, 22))
    await session.commit()
    return account


@pytest.mark.asyncio
async def test_every_store_at_once_adds_up_to_the_group_level(client, auth_headers, world):  # noqa: F811
    body = await _get(client, auth_headers, level="merchant", category_id="all")
    groups = await _get(client, auth_headers, level="group")
    assert body["total"] == groups["total"] == "452.00"
    rows = {r["id"]: r for r in body["rows"]}
    carrefour = rows["CARREFOUR"]
    # 100 + 200 - 50 in Mercado, then the 20 back filed under Estornos.
    assert carrefour["total"] == "230.00" and carrefour["kind"] == "merchant"
    assert carrefour["count"] == 4
    # The category where the store weighs most.
    assert carrefour["category_id"] == str(world["mercado"].id)
    assert carrefour["category_label"] == "Mercado"
    padaria = rows["PADARIA ESTRELA"]
    assert padaria["total"] == "17.00"  # debits only, as on the group level
    assert padaria["category_id"] is None and padaria["category_label"] == "Sem categoria"
    assert "EMPRESA" not in rows  # income is not spending
    assert [r["id"] for r in body["rows"]][:2] == ["CARREFOUR", "ENEL"]
    assert len(carrefour["sparkline"]) == 12 and carrefour["sparkline"][-1] == "230.00"
    assert carrefour["sparkline"][-2] == "40.00"


@pytest.mark.asyncio
async def test_by_account_splits_the_spending_per_account(client, auth_headers, world, card, test_account):  # noqa: F811
    body = await _get(client, auth_headers, level="account")
    rows = {r["id"]: r for r in body["rows"]}
    assert set(rows) == {str(test_account.id), str(card.id)}
    assert rows[str(card.id)]["label"] == "Nubank"  # the name the person chose
    assert rows[str(card.id)]["kind"] == "account"
    assert rows[str(card.id)]["total"] == "100.00"
    assert rows[str(test_account.id)]["total"] == "452.00"
    assert rows[str(test_account.id)]["label"] == "Conta Corrente"
    assert body["total"] == "552.00"
    assert body["rows"][0]["id"] == str(test_account.id)


@pytest.mark.asyncio
async def test_account_filter_narrows_every_level(client, auth_headers, world, card):  # noqa: F811
    only = {"account_id": str(card.id)}
    groups = await _get(client, auth_headers, level="group", **only)
    assert groups["total"] == "100.00"
    assert [(r["label"], r["total"]) for r in groups["rows"]] == [("Casa", "100.00")]
    categories = await _get(
        client, auth_headers, level="category", group_id=str(world["casa"].id), **only
    )
    assert [(r["label"], r["total"]) for r in categories["rows"]] == [
        ("Luz", "60.00"), ("Mercado", "40.00"),
    ]
    stores = await _get(
        client, auth_headers, level="merchant", category_id=str(world["mercado"].id), **only
    )
    assert [(r["id"], r["total"]) for r in stores["rows"]] == [("CARREFOUR", "40.00")]
    every = await _get(client, auth_headers, level="merchant", category_id="all", **only)
    assert every["total"] == "100.00"


@pytest.mark.asyncio
async def test_account_filter_must_be_a_uuid(client, auth_headers, world):  # noqa: F811
    response = await client.get(
        URL, params={**SEP, "level": "group", "account_id": "x"}, headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_compare_with_the_same_period_a_year_ago(
    session, client, auth_headers, world, test_user, test_account  # noqa: F811
):
    _tx(session, test_user, test_account, "Enel", category_id=world["luz"].id,
        amount="70.00", day=date(2025, 9, 7))
    await session.commit()
    body = await _get(client, auth_headers, level="group", compare="year_ago")
    assert body["period"]["previous_from"] == "2025-09-01"
    assert body["period"]["previous_to"] == "2025-09-30"
    assert body["previous_total"] == "70.00"


@pytest.mark.asyncio
async def test_compare_with_a_custom_period(client, auth_headers, world):  # noqa: F811
    body = await _get(
        client, auth_headers, level="group", compare="custom",
        compare_from="2026-08-01", compare_to="2026-08-31",
    )
    assert (body["period"]["previous_from"], body["period"]["previous_to"]) == ("2026-08-01", "2026-08-31")
    casa = next(r for r in body["rows"] if r["label"] == "Casa")
    assert casa["previous_total"] == "40.00"
    assert casa["change"] == pytest.approx((370 - 40) / 40, abs=1e-3)
    # The categories of a group compare against the same period.
    cats = await _get(
        client, auth_headers, level="category", group_id=str(world["casa"].id),
        compare="custom", compare_from="2026-08-01", compare_to="2026-08-31",
    )
    mercado = next(r for r in cats["rows"] if r["label"] == "Mercado")
    assert mercado["previous_total"] == "40.00"


@pytest.mark.asyncio
async def test_custom_compare_needs_both_dates_in_order(client, auth_headers, world):  # noqa: F811
    for extra in ({}, {"compare_from": "2026-08-31", "compare_to": "2026-08-01"}):
        response = await client.get(
            URL, params={**SEP, "level": "group", "compare": "custom", **extra}, headers=auth_headers
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "compare_dates"
