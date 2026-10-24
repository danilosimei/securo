"""Real PostgreSQL report/API/MCP checks using the shared disposable schemas."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_async_session
from app.main import app
from app.models.category import Category
from app.models.transaction import Transaction
import mcp_server.tools  # noqa: F401 -- registers the real MCP handlers
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user/data fixtures start in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _countable(session, transactions):
    """Make the shared fixture rows countable by the report rules: they are USD
    with no converted value (kept out of the sums) and the salary sits in a
    category that is not `income` (it would ease spending)."""
    for txn in transactions:
        txn.amount_primary = txn.amount
        if txn.type == "credit" and txn.category_id is not None:
            (await session.get(Category, txn.category_id)).report_kind = "income"
    await session.commit()


async def test_income_expenses_api_endpoint(client, auth_headers, session, test_transactions):
    """GET /reports/income-expenses returns valid response."""
    await _countable(session, test_transactions)
    response = await client.get(
        "/api/reports/income-expenses",
        params={"period": "this_month", "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["meta"]["type"] == "income_expenses"
    assert data["meta"]["series_keys"] == [
        "income", "expenses", "projectedIncome", "projectedExpenses"
    ]
    assert "summary" in data
    assert "trend" in data

    # Summary should have income, expenses, netIncome breakdowns
    breakdown_keys = [b["key"] for b in data["summary"]["breakdowns"]]
    assert "income" in breakdown_keys
    assert "expenses" in breakdown_keys
    assert "netIncome" in breakdown_keys

    # Nonzero fixtures make grouping and exclusion failures observable.
    totals = {b["key"]: b["value"] for b in data["summary"]["breakdowns"]}
    assert totals["income"] == pytest.approx(8150)
    assert totals["expenses"] == pytest.approx(110.40)

    # Verify math: net income = income - expenses
    breakdowns = {b["key"]: b["value"] for b in data["summary"]["breakdowns"]}
    assert abs(breakdowns["netIncome"] - (breakdowns["income"] - breakdowns["expenses"])) < 0.01

    # Each trend point has income/expenses breakdowns
    for dp in data["trend"]:
        assert "income" in dp["breakdowns"]
        assert "expenses" in dp["breakdowns"]
        # value = net income = income - expenses
        expected_net = dp["breakdowns"]["income"] - dp["breakdowns"]["expenses"]
        assert abs(dp["value"] - expected_net) < 0.01


async def test_income_expenses_excludes_opening_balance(client, auth_headers):
    """Income expenses report excludes opening balance transactions."""
    # Create account with opening balance
    acc_resp = await client.post(
        "/api/accounts",
        json={"name": "IE Test", "type": "checking", "balance": 10000.00, "currency": "BRL"},
        headers=auth_headers,
    )
    assert acc_resp.status_code == 201

    response = await client.get(
        "/api/reports/income-expenses",
        params={"months": 1, "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    # Opening balance should NOT appear as income
    breakdowns = {b["key"]: b["value"] for b in data["summary"]["breakdowns"]}
    assert breakdowns["income"] == 0.0


async def test_income_expenses_has_category_trend(
    client, auth_headers, session, test_transactions, test_categories
):
    """GET /reports/income-expenses response includes category_trend array."""
    await _countable(session, test_transactions)
    response = await client.get(
        "/api/reports/income-expenses",
        params={"period": "this_month", "interval": "monthly"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert "category_trend" in data
    assert isinstance(data["category_trend"], list)

    # Each item should have the expected shape
    for item in data["category_trend"]:
        assert "key" in item
        assert "label" in item
        assert "color" in item
        assert "total" in item
        assert "group" in item
        assert item["group"] in ("income", "expenses")
        assert "series" in item
        assert isinstance(item["series"], list)
        for point in item["series"]:
            assert "date" in point
            assert "value" in point

    categories = {item["key"]: item for item in data["category_trend"]}
    for category, expected in zip(test_categories, (45, 25.50, 8000), strict=True):
        item = categories[str(category.id)]
        assert item["label"] == category.name
        assert item["color"] == category.color
        assert item["total"] == pytest.approx(expected)
        assert sum(point["value"] for point in item["series"]) == pytest.approx(expected)


async def test_income_expenses_excludes_transfers(
    client, auth_headers, session, test_account, test_transactions,
):
    await _countable(session, test_transactions)
    this_month = {"period": "this_month"}
    before = await client.get("/api/reports/income-expenses", params=this_month, headers=auth_headers)
    assert before.status_code == 200
    destination = await client.post(
        "/api/accounts",
        json={"name": "Transfer destination", "type": "savings", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    assert destination.status_code == 201
    transfer = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": destination.json()["id"],
            "description": "Synthetic transfer", "amount": 500,
            "date": date.today().isoformat(),
        },
        headers=auth_headers,
    )
    assert transfer.status_code == 201
    legs = list(await session.scalars(select(Transaction).where(Transaction.transfer_pair_id.is_not(None))))
    assert len(legs) == 2
    assert {leg.amount for leg in legs} == {Decimal("500")}
    response = await client.get("/api/reports/income-expenses", params=this_month, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["summary"]["breakdowns"] == before.json()["summary"]["breakdowns"]
    totals = {b["key"]: b["value"] for b in response.json()["summary"]["breakdowns"]}
    assert totals["income"] == pytest.approx(8150)
    assert totals["expenses"] == pytest.approx(110.40)


async def test_monthly_trend_numeric_fields(client, auth_headers, session, test_transactions):
    today = date.today()
    previous_month = today.replace(day=1) - timedelta(days=1)
    test_transactions[0].date = previous_month
    test_transactions[0].effective_date = previous_month
    await _countable(session, test_transactions)
    response = await client.get("/api/dashboard/monthly-trend", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    for item in data:
        assert isinstance(item["income"], (int, float))
        assert isinstance(item["expenses"], (int, float))
    buckets = {item["month"]: item for item in data}
    assert buckets[previous_month.strftime("%Y-%m")]["expenses"] == pytest.approx(25.50)
    current = buckets[today.strftime("%Y-%m")]
    assert current["income"] == pytest.approx(8150)
    assert current["expenses"] == pytest.approx(84.90)


async def test_aggregate_by_month(session, test_user, test_transactions):
    today = date.today()
    previous_month = today.replace(day=1) - timedelta(days=1)
    test_transactions[0].date = previous_month
    await session.commit()
    result = await REGISTRY["aggregate"].handler(
        session=session,
        ctx=CallContext(user_id=test_user.id, conversation_id=uuid.uuid4()),
        metric="count", group_by="month",
    )
    assert "items" in result
    for item in result["items"]:
        if item["bucket"]:
            assert len(item["bucket"]) == 7 and item["bucket"][4] == "-"
    assert {item["bucket"]: item["value"] for item in result["items"]} == {
        previous_month.strftime("%Y-%m"): 1,
        today.strftime("%Y-%m"): 4,
    }

