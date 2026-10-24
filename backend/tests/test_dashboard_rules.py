"""The dashboard's income/expenses numbers follow the shared report rules."""
import uuid
from datetime import date, timedelta

import pytest

from app.models.category import Category
from app.models.category_group import CategoryGroup
from tests.test_categorization_api import _tx


def _month_start(back: int = 0) -> date:
    d = date.today().replace(day=1)
    for _ in range(back):
        d = (d - timedelta(days=1)).replace(day=1)
    return d


PREV = _month_start(1)
PREV_END = _month_start(0) - timedelta(days=1)


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
    estornos = cat("Estornos", group_id=lazer.id, report_kind="refund")
    salario = cat("Salario", report_kind="income")
    aporte = cat("Aporte", treat_as_transfer=True)
    await session.flush()

    def add(category, amount, day, type="debit", **extra):
        return _tx(
            session, test_user, test_account, f"{type} {amount}",
            category_id=category.id if category else None,
            amount=amount, type=type, day=day, **extra,
        )

    d5, d12 = PREV.replace(day=5), PREV.replace(day=12)
    add(salario, "5000.00", d5, "credit")
    add(None, "30.00", d5, "credit")
    add(mercado, "100.00", d5)
    add(mercado, "200.00", d12)
    add(mercado, "50.00", d12, "credit")
    add(cinema, "80.00", d5)
    add(estornos, "20.00", d12, "credit")
    add(aporte, "500.00", d5)
    add(mercado, "999.00", d5, status="pending")
    await session.commit()
    return {"ws_id": ws_id}


@pytest.mark.asyncio
async def test_summary_month_follows_the_rules(client, auth_headers, world):
    response = await client.get(
        "/api/dashboard/summary", params={"month": PREV.isoformat()}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["monthly_income"] == 5030.0  # salary + credit still to check
    assert body["monthly_expenses"] == 310.0  # 100 + 200 - 50 + 80 - 20
    assert body["monthly_income_primary"] == 5030.0
    assert body["monthly_expenses_primary"] == 310.0


@pytest.mark.asyncio
async def test_summary_keeps_the_forecast_on_top_of_the_actuals(
    client, auth_headers, world, session, test_user, test_account
):
    # A pending row of the current month is forecast, never actual.
    _tx(session, test_user, test_account, "pending", amount="70.00", day=date.today(), status="pending")
    await session.commit()
    body = (await client.get("/api/dashboard/summary", headers=auth_headers)).json()
    assert body["monthly_expenses"] == 0.0
    assert body["projected_expenses"] == 70.0
    assert body["projected_expenses_primary"] == 70.0


@pytest.mark.asyncio
async def test_summary_leaves_out_rows_without_a_primary_value(
    client, auth_headers, world, session, test_user, test_account
):
    tx = _tx(session, test_user, test_account, "usd", amount="40.00", day=PREV.replace(day=6))
    tx.currency = "USD"
    await session.commit()
    body = (
        await client.get(
            "/api/dashboard/summary", params={"month": PREV.isoformat()}, headers=auth_headers
        )
    ).json()
    assert body["monthly_expenses"] == 310.0  # the USD row is not added at face value


@pytest.mark.asyncio
async def test_monthly_trend_follows_the_rules(client, auth_headers, world):
    body = (await client.get("/api/dashboard/monthly-trend", headers=auth_headers)).json()
    assert body == [
        {"month": f"{PREV.year:04d}-{PREV.month:02d}", "income": 5030.0, "expenses": 310.0}
    ]


@pytest.mark.asyncio
async def test_spending_by_category_nets_refunds_and_has_no_negative_line(
    client, auth_headers, world
):
    body = (
        await client.get(
            "/api/dashboard/spending-by-category",
            params={"month": PREV.isoformat()},
            headers=auth_headers,
        )
    ).json()
    totals = {row["category_name"]: row["total"] for row in body}
    # the refund category (-20) eases Cinema, the biggest of its own group
    assert totals == {"Mercado": 250.0, "Cinema": 60.0}
    assert all(row["total"] > 0 for row in body)
    assert sum(row["percentage"] for row in body) == pytest.approx(100.0)
    assert [row["category_name"] for row in body] == ["Mercado", "Cinema"]


@pytest.mark.asyncio
async def test_dashboard_expenses_match_the_reports_breakdown_total(client, auth_headers, world):
    summary = (
        await client.get(
            "/api/dashboard/summary", params={"month": PREV.isoformat()}, headers=auth_headers
        )
    ).json()
    breakdown = (
        await client.get(
            "/api/reports/breakdown",
            params={
                "level": "group",
                "period": "custom",
                "from": PREV.isoformat(),
                "to": PREV_END.isoformat(),
            },
            headers=auth_headers,
        )
    ).json()
    assert f"{summary['monthly_expenses_primary']:.2f}" == breakdown["total"]
    spending = (
        await client.get(
            "/api/dashboard/spending-by-category",
            params={"month": PREV.isoformat()},
            headers=auth_headers,
        )
    ).json()
    assert f"{sum(row['total'] for row in spending):.2f}" == breakdown["total"]
