"""GET /api/reports/income-expenses on the shared report rules."""
import uuid
from datetime import date

import pytest

from app.models.category import Category
from app.models.category_group import CategoryGroup
from tests.test_categorization_api import _tx

URL = "/api/reports/income-expenses"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr("app.services.reports.income_expenses.app_today", lambda: date(2026, 10, 21))


@pytest.fixture
async def world(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    ws_id, user_id = test_workspace.id, test_user.id
    lazer = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Lazer")
    casa = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Casa")
    session.add_all([lazer, casa])
    await session.flush()

    def cat(name, **kw):
        c = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name=name, **kw)
        session.add(c)
        return c

    mercado = cat("Mercado", group_id=casa.id)
    cinema = cat("Cinema", group_id=lazer.id)
    estornos = cat("Estornos & Reembolsos", group_id=lazer.id, report_kind="refund")
    salario = cat("Salário", report_kind="income")
    aporte = cat("Aporte", treat_as_transfer=True)
    await session.flush()

    def add(category, amount, day, type="debit", **extra):
        return _tx(
            session, test_user, test_account, f"{type} {amount}",
            category_id=category.id if category else None,
            amount=amount, type=type, day=day, **extra,
        )

    add(salario, "5000.00", date(2026, 9, 5), "credit")
    add(None, "30.00", date(2026, 9, 6), "credit")  # income to check
    add(mercado, "100.00", date(2026, 9, 5))
    add(mercado, "200.00", date(2026, 9, 12))
    add(mercado, "50.00", date(2026, 9, 20), "credit")  # refund in the same category
    add(cinema, "80.00", date(2026, 9, 8))
    add(estornos, "20.00", date(2026, 9, 9), "credit")
    add(None, "10.00", date(2026, 9, 6))
    add(aporte, "500.00", date(2026, 9, 7))  # own money: not spending
    add(mercado, "999.00", date(2026, 9, 25), status="pending")  # not posted
    add(mercado, "300.00", date(2026, 8, 10))
    add(salario, "4000.00", date(2026, 8, 5), "credit")
    add(mercado, "70.00", date(2026, 10, 3))  # current month
    await session.commit()
    return {"account": test_account}


def _break(body, key):
    return next(b["value"] for b in body["summary"]["breakdowns"] if b["key"] == key)


@pytest.mark.asyncio
async def test_summary_follows_the_rules(client, auth_headers, world):
    response = await client.get(URL, params={"period": "last_month"}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert _break(body, "income") == 5030.0  # salary + credit still to check
    # 100 + 200 - 50 + 80 - 20 + 10; pending, own-money and projections are out
    assert _break(body, "expenses") == 320.0
    assert _break(body, "projectedIncome") == 0.0
    assert _break(body, "projectedExpenses") == 0.0
    assert body["summary"]["primary_value"] == 4710.0
    assert [p["date"] for p in body["trend"]] == ["2026-09"]


@pytest.mark.asyncio
async def test_change_percent_compares_with_the_previous_period(client, auth_headers, world):
    response = await client.get(URL, params={"period": "last_month"}, headers=auth_headers)
    summary = response.json()["summary"]
    # August: 4000 - 300 = 3700 (not first x last point of the trend)
    assert summary["change_amount"] == 1010.0
    assert summary["change_percent"] == pytest.approx(27.3, abs=0.05)


@pytest.mark.asyncio
async def test_change_percent_is_null_without_a_previous_net(client, auth_headers, world):
    response = await client.get(URL, params={"period": "last_3m"}, headers=auth_headers)
    # Apr-Jun has no rows at all
    assert response.json()["summary"]["change_percent"] is None


@pytest.mark.asyncio
async def test_composition_has_no_projection_no_investments_and_no_refund_line(
    client, auth_headers, world
):
    body = (await client.get(URL, params={"period": "last_month"}, headers=auth_headers)).json()
    comp = body["composition"]
    assert {c["group"] for c in comp} == {"income", "expenses"}
    assert all(c["value"] > 0 for c in comp)
    names = {(c["group"], c["label"]): c["value"] for c in comp}
    # The refund category (-20) eases the biggest spending of its own group
    # (Lazer: Cinema 80 -> 60); it has no line of its own.
    assert ("expenses", "Estornos & Reembolsos") not in names
    assert names[("expenses", "Cinema")] == 60.0
    assert names[("expenses", "Mercado")] == 250.0
    assert names[("expenses", "Uncategorized")] == 10.0
    assert names[("income", "Salário")] == 5000.0
    assert names[("income", "Uncategorized")] == 30.0  # the credit to check
    assert sum(c["value"] for c in comp if c["group"] == "expenses") == 320.0
    assert ("expenses", "Aporte") not in names


@pytest.mark.asyncio
async def test_category_trend_follows_the_rules(client, auth_headers, world):
    body = (await client.get(URL, params={"period": "last_month"}, headers=auth_headers)).json()
    expenses = {c["label"]: c for c in body["category_trend"] if c["group"] == "expenses"}
    assert expenses["Mercado"]["total"] == 250.0
    assert [(p["date"], p["value"]) for p in expenses["Mercado"]["series"]] == [("2026-09", 250.0)]
    income = {c["label"]: c["total"] for c in body["category_trend"] if c["group"] == "income"}
    assert income == {"Salário": 5000.0, "Uncategorized": 30.0}


@pytest.mark.asyncio
async def test_trend_points_carry_their_own_composition(client, auth_headers, world):
    body = (await client.get(URL, params={"period": "last_3m"}, headers=auth_headers)).json()
    points = {p["date"]: p for p in body["trend"]}
    assert list(points) == ["2026-07", "2026-08", "2026-09"]
    assert points["2026-07"]["breakdowns"]["income"] == 0.0
    assert points["2026-08"]["breakdowns"] == {
        "income": 4000.0, "expenses": 300.0, "projectedIncome": 0.0, "projectedExpenses": 0.0,
    }
    august = [(c["label"], c["value"]) for c in points["2026-08"]["composition"] if c["group"] == "expenses"]
    assert august == [("Mercado", 300.0)]


@pytest.mark.asyncio
async def test_months_twelve_is_twelve_closed_months(client, auth_headers, world):
    body = (await client.get(URL, params={"months": 12}, headers=auth_headers)).json()
    labels = [p["date"] for p in body["trend"]]
    assert len(labels) == 12
    assert labels[0] == "2025-10" and labels[-1] == "2026-09"  # October (partial) is out


@pytest.mark.asyncio
async def test_months_other_than_presets_end_last_month(client, auth_headers, world):
    body = (await client.get(URL, params={"months": 5}, headers=auth_headers)).json()
    assert [p["date"] for p in body["trend"]] == [
        "2026-05", "2026-06", "2026-07", "2026-08", "2026-09",
    ]


@pytest.mark.asyncio
async def test_period_wins_over_months(client, auth_headers, world):
    params = {"months": 24, "period": "last_6m"}
    body = (await client.get(URL, params=params, headers=auth_headers)).json()
    assert len(body["trend"]) == 6


@pytest.mark.asyncio
async def test_days_is_a_rolling_window_up_to_today(client, auth_headers, world):
    body = (await client.get(URL, params={"days": 30}, headers=auth_headers)).json()
    # 2026-09-22 .. 2026-10-21: the pending row of Sep 25 is out, the Sep-20 refund is before
    assert [p["date"] for p in body["trend"]] == ["2026-09", "2026-10"]
    assert _break(body, "expenses") == 70.0
    assert _break(body, "income") == 0.0


@pytest.mark.asyncio
async def test_this_month_is_partial(client, auth_headers, world):
    body = (await client.get(URL, params={"period": "this_month"}, headers=auth_headers)).json()
    assert _break(body, "expenses") == 70.0
    assert [p["date"] for p in body["trend"]] == ["2026-10"]


@pytest.mark.asyncio
async def test_custom_dates_still_work(client, auth_headers, world):
    params = {"start_date": "2026-09-01", "end_date": "2026-09-10"}
    body = (await client.get(URL, params=params, headers=auth_headers)).json()
    assert _break(body, "income") == 5030.0
    assert _break(body, "expenses") == 100.0 + 80.0 + 10.0 - 20.0


@pytest.mark.asyncio
async def test_account_filter_narrows_to_those_accounts(client, auth_headers, world, session):
    params = {"period": "last_month", "account_ids": [str(uuid.uuid4())]}
    body = (await client.get(URL, params=params, headers=auth_headers)).json()
    assert _break(body, "income") == 0.0 and _break(body, "expenses") == 0.0
    params = {"period": "last_month", "account_ids": [str(world["account"].id)]}
    body = (await client.get(URL, params=params, headers=auth_headers)).json()
    assert _break(body, "expenses") == 320.0


@pytest.mark.asyncio
async def test_unknown_period_is_rejected(client, auth_headers, world):
    response = await client.get(URL, params={"period": "last_13m"}, headers=auth_headers)
    assert response.status_code == 422
