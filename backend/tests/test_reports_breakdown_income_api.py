"""GET /api/reports/breakdown?flow=income: where the money came from (income
categories plus the credits still without a category, then who paid, by
account, against another period), and, on the spending side, the money of a
group that went into the person's own assets."""
import uuid
from datetime import date

import pytest

from app.models.category import Category
from app.models.category_group import CategoryGroup
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
    renda = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Renda")
    moradia = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Moradia")
    lazer = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Lazer")
    session.add_all([renda, moradia, lazer])
    await session.flush()

    def cat(name, **kw):
        c = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name=name, **kw)
        session.add(c)
        return c

    salario = cat("Salário", group_id=renda.id, report_kind="income")
    freelas = cat("Freelas", report_kind="income")
    aluguel = cat("Aluguel", group_id=moradia.id)
    compra = cat("Compra do apartamento", group_id=moradia.id, treat_as_transfer=True)
    escondida = cat("Escondida", group_id=moradia.id, is_ignored=True)
    cinema = cat("Cinema", group_id=lazer.id)
    investimentos = cat("Investimentos", treat_as_transfer=True)
    await session.flush()

    def add(desc, category, amount, day, type="debit"):
        return _tx(
            session, test_user, test_account, desc,
            category_id=category.id if category else None,
            amount=amount, type=type, day=day,
        )

    add("Empresa", salario, "5000.00", date(2026, 9, 5), "credit")
    add("Empresa", salario, "4000.00", date(2026, 8, 5), "credit")  # previous period
    add("Cliente Azul", freelas, "800.00", date(2026, 9, 10), "credit")
    add("Cliente Azul", freelas, "100.00", date(2026, 9, 11))  # gave some back
    add("Fulano de Tal", None, "300.00", date(2026, 9, 12), "credit")  # to check
    add("Padaria Estrela", None, "10.00", date(2026, 9, 6))  # spending, not income
    add("Cinemark", cinema, "80.00", date(2026, 9, 8))
    add("Cinemark", cinema, "20.00", date(2026, 9, 9), "credit")  # a refund, not income
    add("Corretora", investimentos, "1000.00", date(2026, 9, 15), "credit")  # own money
    add("Imobiliária", aluguel, "1000.00", date(2026, 9, 3))
    add("Construtora", compra, "250000.00", date(2026, 9, 20))
    add("Construtora", compra, "1000.00", date(2026, 9, 21), "credit")  # part came back
    add("Construtora", compra, "9000.00", date(2026, 8, 20))  # previous period
    add("Escondida", escondida, "50.00", date(2026, 9, 4))
    await session.commit()
    return {
        "renda": renda, "moradia": moradia, "lazer": lazer, "salario": salario,
        "freelas": freelas, "compra": compra, "aluguel": aluguel,
    }


async def _get(client, auth_headers, **params):
    response = await client.get(URL, params={**SEP, **params}, headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_income_starts_at_categories_with_a_line_to_check(client, auth_headers, world):
    body = await _get(client, auth_headers, level="category", flow="income")
    assert [r["label"] for r in body["rows"]] == ["Salário", "Freelas", "A conferir"]
    salario, freelas, check = body["rows"]
    assert salario["id"] == str(world["salario"].id) and salario["kind"] == "category"
    assert salario["total"] == "5000.00" and salario["previous_total"] == "4000.00"
    assert salario["count"] == 1 and salario["change"] == 0.25
    assert freelas["total"] == "700.00"  # 800 received - 100 given back
    assert freelas["count"] == 2
    # Credits without a category: income still to check; their debits are spending.
    assert check["id"] is None and check["total"] == "300.00" and check["count"] == 1
    assert body["total"] == "6000.00" and body["previous_total"] == "4000.00"
    assert salario["share"] == pytest.approx(0.833, abs=0.001)
    assert len(salario["sparkline"]) == 12 and salario["sparkline"][-1] == "5000.00"
    assert salario["sparkline"][-2] == "4000.00"
    assert body["to_assets"] == []


@pytest.mark.asyncio
async def test_income_total_matches_the_overview(client, auth_headers, world):
    body = await _get(client, auth_headers, level="category", flow="income")
    overview = await client.get(
        "/api/reports/overview", params=SEP, headers=auth_headers
    )
    assert overview.status_code == 200, overview.text
    assert overview.json()["kpis"]["income"] == body["total"]


@pytest.mark.asyncio
async def test_income_categories_by_group_name(client, auth_headers, world):
    body = await _get(client, auth_headers, level="category", flow="income", group_id="all")
    labels = {r["label"]: r.get("group_label") for r in body["rows"]}
    assert labels == {"Salário": "Renda", "Freelas": "Sem grupo", "A conferir": None}


@pytest.mark.asyncio
async def test_income_category_opens_who_paid(client, auth_headers, world):
    body = await _get(
        client, auth_headers, level="merchant", flow="income", category_id=str(world["freelas"].id)
    )
    assert [(r["id"], r["total"], r["count"]) for r in body["rows"]] == [("CLIENTE AZUL", "700.00", 2)]
    assert body["total"] == "700.00"


@pytest.mark.asyncio
async def test_income_to_check_opens_who_paid_credits_only(client, auth_headers, world):
    body = await _get(client, auth_headers, level="merchant", flow="income", category_id="none")
    assert [(r["label"], r["total"], r["count"]) for r in body["rows"]] == [("Fulano de Tal", "300.00", 1)]


@pytest.mark.asyncio
async def test_income_of_a_spending_category_is_empty(client, auth_headers, world):
    body = await _get(
        client, auth_headers, level="merchant", flow="income", category_id=str(world["aluguel"].id)
    )
    assert body["rows"] == [] and body["total"] == "0.00"


@pytest.mark.asyncio
async def test_every_payer_at_once(client, auth_headers, world):
    body = await _get(client, auth_headers, level="merchant", flow="income", category_id="all")
    rows = {r["id"]: r for r in body["rows"]}
    assert set(rows) == {"EMPRESA", "CLIENTE AZUL", "FULANO DE TAL"}
    assert rows["EMPRESA"]["total"] == "5000.00"
    assert rows["EMPRESA"]["category_id"] == str(world["salario"].id)
    assert rows["EMPRESA"]["category_label"] == "Salário"
    assert rows["CLIENTE AZUL"]["total"] == "700.00"
    assert rows["FULANO DE TAL"]["category_id"] is None
    assert rows["FULANO DE TAL"]["category_label"] == "A conferir"
    assert body["total"] == "6000.00"


@pytest.mark.asyncio
async def test_income_by_account(client, auth_headers, world, test_account):
    body = await _get(client, auth_headers, level="account", flow="income")
    assert [(r["id"], r["total"]) for r in body["rows"]] == [(str(test_account.id), "6000.00")]


@pytest.mark.asyncio
async def test_income_compared_with_a_year_ago(client, auth_headers, world):
    body = await _get(client, auth_headers, level="category", flow="income", compare="year_ago")
    assert body["period"]["previous_from"] == "2025-09-01"
    assert body["total"] == "6000.00" and body["previous_total"] == "0.00"


@pytest.mark.asyncio
async def test_income_has_no_group_level(client, auth_headers, world):
    response = await client.get(
        URL, params={**SEP, "level": "group", "flow": "income"}, headers=auth_headers
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "level_unknown"


@pytest.mark.asyncio
async def test_expense_is_the_default_flow(client, auth_headers, world):
    default = await _get(client, auth_headers, level="group")
    expense = await _get(client, auth_headers, level="group", flow="expense")
    assert default == expense
    # Income, own-money moves and the ignored category stay out of spending.
    assert default["total"] == "1070.00"  # 1000 rent + 60 cinema + 10 bakery


@pytest.mark.asyncio
async def test_group_shows_what_went_to_assets(client, auth_headers, world):
    body = await _get(client, auth_headers, level="category", group_id=str(world["moradia"].id))
    assert [r["label"] for r in body["rows"]] == ["Aluguel"]
    assert body["total"] == "1000.00"  # the purchase is not spending
    assert body["to_assets"] == [
        {"id": str(world["compra"].id), "label": "Compra do apartamento", "total": "249000.00"}
    ]


@pytest.mark.asyncio
async def test_group_without_asset_categories_shows_nothing(client, auth_headers, world):
    body = await _get(client, auth_headers, level="category", group_id=str(world["lazer"].id))
    assert body["to_assets"] == []
    groups = await _get(client, auth_headers, level="group")
    assert groups["to_assets"] == []


@pytest.mark.asyncio
async def test_moving_money_between_own_accounts_is_not_going_to_assets(
    client, auth_headers, world, session, test_user, test_account, test_workspace
):
    """The default "Transfers" category only moves money between the person's
    own accounts, and a category where more came back than left sent nothing
    to the assets: neither shows up."""
    financas = CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Finanças"
    )
    session.add(financas)
    await session.flush()

    def cat(name, **kw):
        c = Category(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            name=name, group_id=financas.id, treat_as_transfer=True, **kw,
        )
        session.add(c)
        return c

    transferencias = cat("Transferências", is_system=True)
    caixinha = cat("Caixinha")
    poupanca = cat("Poupança")
    await session.flush()
    for desc, category, amount, type in [
        ("Para o Nubank", transferencias, "3000.00", "debit"),
        ("Do BB", transferencias, "500.00", "credit"),
        ("Aplicação", caixinha, "700.00", "debit"),
        ("Resgate", poupanca, "400.00", "credit"),
    ]:
        _tx(session, test_user, test_account, desc, category_id=category.id,
            amount=amount, type=type, day=date(2026, 9, 14))
    await session.commit()

    body = await _get(client, auth_headers, level="category", group_id=str(financas.id))
    assert body["to_assets"] == [{"id": str(caixinha.id), "label": "Caixinha", "total": "700.00"}]


@pytest.mark.asyncio
async def test_assets_follow_the_account_filter(client, auth_headers, world):
    body = await _get(
        client, auth_headers, level="category", group_id=str(world["moradia"].id),
        account_id=str(uuid.uuid4()),
    )
    assert body["to_assets"] == []
