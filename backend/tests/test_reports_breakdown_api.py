"""GET /api/reports/breakdown, level by group."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from tests.test_categorization_api import _tx

URL = "/api/reports/breakdown"
SEP = {"period": "custom", "from": "2026-09-01", "to": "2026-09-30"}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr("app.api.reports_breakdown.app_today", lambda: date(2026, 10, 21))


@pytest.fixture
async def world(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    ws_id, user_id = test_workspace.id, test_user.id
    casa = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Casa")
    lazer = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Lazer")
    session.add_all([casa, lazer])
    await session.flush()

    def cat(name, **kw):
        c = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name=name, **kw)
        session.add(c)
        return c

    mercado = cat("Mercado", group_id=casa.id)
    cinema = cat("Cinema", group_id=lazer.id)
    estornos = cat("Estornos & Reembolsos", group_id=lazer.id, report_kind="refund")
    sem_grupo = cat("Avulsa")
    salario = cat("Salário", report_kind="income")
    await session.flush()

    def add(category, amount, day, type="debit"):
        return _tx(
            session, test_user, test_account, f"{type} {amount}",
            category_id=category.id if category else None,
            amount=amount, type=type, day=day,
        )

    add(mercado, "100.00", date(2026, 9, 5))
    add(mercado, "200.00", date(2026, 9, 12))
    add(mercado, "50.00", date(2026, 9, 20), "credit")  # estorno na mesma categoria
    add(cinema, "80.00", date(2026, 9, 8))
    add(estornos, "20.00", date(2026, 9, 9), "credit")
    add(salario, "5000.00", date(2026, 9, 5), "credit")
    add(None, "10.00", date(2026, 9, 6))
    add(sem_grupo, "5.00", date(2026, 9, 7))
    add(mercado, "150.00", date(2026, 8, 15))  # período anterior
    await session.commit()
    return {"ws_id": ws_id, "casa": casa, "lazer": lazer, "estornos": estornos}


@pytest.mark.asyncio
async def test_rows_by_group_with_refunds_comparison_and_sparkline(client, auth_headers, world):
    response = await client.get(URL, params={"level": "group", **SEP}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["period"] == {
        "from": "2026-09-01", "to": "2026-09-30", "partial": False,
        "previous_from": "2026-08-02", "previous_to": "2026-08-31",
    }
    assert body["currency"] == "BRL"
    assert body["total"] == "325.00"  # 250 + 80 + 10 + 5 - 20; the salary is not spending
    assert body["previous_total"] == "150.00"
    assert body["unconverted_count"] == 0

    rows = body["rows"]
    assert [r["label"] for r in rows] == ["Casa", "Lazer", "Sem categoria", "Sem grupo", "Estornos & Reembolsos"]
    casa, lazer, no_category, no_group, refunds = rows
    assert casa["id"] == str(world["casa"].id) and casa["kind"] == "group"
    assert casa["total"] == "250.00"  # 100 + 200 - 50
    assert casa["count"] == 3
    assert casa["share"] == pytest.approx(0.769, abs=1e-3)
    assert casa["previous_total"] == "150.00"
    assert casa["change"] == pytest.approx(0.667, abs=1e-3)
    # The refund category sits in Lazer but shows on its own line, so the
    # reduction stays visible instead of hiding inside its group.
    assert lazer["total"] == "80.00"
    assert lazer["previous_total"] == "0.00" and lazer["change"] is None
    assert no_category["id"] is None and no_category["total"] == "10.00"
    assert no_group["id"] is None and no_group["total"] == "5.00"
    assert refunds["id"] == str(world["estornos"].id) and refunds["kind"] == "category"
    assert refunds["total"] == "-20.00"
    assert refunds["share"] is None  # a negative row has no share

    assert len(casa["sparkline"]) == 12
    assert casa["sparkline"][-1] == "250.00"  # September
    assert casa["sparkline"][-2] == "150.00"  # August
    assert casa["sparkline"][0] == "0.00"
    assert Decimal(casa["sparkline"][-1]) == Decimal(casa["total"])


@pytest.mark.asyncio
async def test_preset_period_uses_closed_months(client, auth_headers, world):
    response = await client.get(
        URL, params={"level": "group", "period": "last_3m"}, headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["period"]["from"] == "2026-07-01"
    assert body["period"]["to"] == "2026-09-30"
    assert body["period"]["partial"] is False
    assert body["total"] == "475.00"  # August (150) joins September (325)


@pytest.mark.asyncio
async def test_this_month_is_partial(client, auth_headers, world):
    response = await client.get(
        URL, params={"level": "group", "period": "this_month"}, headers=auth_headers
    )
    body = response.json()
    assert body["period"]["partial"] is True
    assert body["period"]["from"] == "2026-10-01" and body["period"]["to"] == "2026-10-21"
    assert body["rows"] == [] and body["total"] == "0.00"
    assert body["previous_total"] == "325.00"  # 1-21 September, same days of last month


@pytest.mark.asyncio
async def test_unknown_level_or_period_is_refused(client, auth_headers, world):
    assert (await client.get(URL, params={"level": "x", **SEP}, headers=auth_headers)).status_code == 422
    assert (
        await client.get(URL, params={"level": "group", "period": "x"}, headers=auth_headers)
    ).status_code == 422


@pytest.mark.asyncio
async def test_custom_without_dates_is_refused(client, auth_headers, world):
    response = await client.get(
        URL, params={"level": "group", "period": "custom"}, headers=auth_headers
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "period_custom_dates"


@pytest.mark.asyncio
async def test_another_workspace_does_not_leak(session, client, auth_headers, world):
    other = User(
        id=uuid.uuid4(), email="outro@example.com", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True,
        preferences={"currency_display": "BRL"},
    )
    session.add(other)
    await session.flush()
    other_ws = Workspace(
        id=uuid.uuid4(), name="Outro", kind="personal", created_by_user_id=other.id,
        default_currency="BRL", locale="pt-BR",
    )
    session.add(other_ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_ws.id, user_id=other.id, role="owner"))
    account = Account(
        id=uuid.uuid4(), user_id=other.id, workspace_id=other_ws.id, name="Conta",
        type="checking", balance=Decimal("0"), currency="BRL",
    )
    group = CategoryGroup(id=uuid.uuid4(), user_id=other.id, workspace_id=other_ws.id, name="Secreto")
    session.add_all([account, group])
    await session.flush()
    secret = Category(
        id=uuid.uuid4(), user_id=other.id, workspace_id=other_ws.id, name="Cofre", group_id=group.id
    )
    session.add(secret)
    await session.flush()
    _tx(session, other, account, "gasto alheio", category_id=secret.id, amount="999.00",
        day=date(2026, 9, 10))
    await session.commit()

    body = (await client.get(URL, params={"level": "group", **SEP}, headers=auth_headers)).json()
    assert "Secreto" not in [r["label"] for r in body["rows"]]
    assert body["total"] == "325.00"
