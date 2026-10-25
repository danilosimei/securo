"""GET /api/reports/overview: KPIs, changes, highlights, monthly, money map."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.category import Category
from app.models.category_group import CategoryGroup
from tests.test_categorization_api import _suggest, _tx

URL = "/api/reports/overview"
SIX = {"period": "last_6m"}  # Apr..Sep 2026, today = 2026-10-21


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr("app.api.reports_breakdown.app_today", lambda: date(2026, 10, 21))


@pytest.fixture
async def world(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    ws_id, user_id = test_workspace.id, test_user.id

    def group(name):
        g = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name=name)
        session.add(g)
        return g

    casa, carro, pets = group("Casa"), group("Carro"), group("Pets")
    await session.flush()

    def cat(name, group=None, **kw):
        c = Category(
            id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name=name,
            group_id=group.id if group else None, **kw,
        )
        session.add(c)
        return c

    cats = {
        "mercado": cat("Mercado", casa),
        "combustivel": cat("Combustível", carro),
        "vet": cat("Veterinário", pets),
        "streaming": cat("Streaming", casa),
        "salario": cat("Salário", report_kind="income"),
    }
    await session.flush()

    def add(desc, category, amount, day, type="debit", **extra):
        return _tx(
            session, test_user, test_account, desc,
            category_id=cats[category].id if category else None,
            amount=str(amount), type=type, day=day, **extra,
        )

    def monthly(desc, category, amounts, first_month=4, day=5, type="debit"):
        for offset, amount in enumerate(amounts):
            add(desc, category, amount, date(2026, first_month + offset, day), type)

    # A steady base, Apr..Sep: 1000 of groceries (a different store each
    # month, so it is not a fixed cost) and 5000 of salary.
    for offset, store in enumerate(["Atacadao", "Carrefour", "Pao de Acucar", "Extra", "Dia", "Mambo"]):
        add(store, "mercado", 1000, date(2026, 4 + offset, 8))
    monthly("Empresa", "salario", [5000] * 6, day=2, type="credit")
    await session.commit()
    return {
        "add": add, "monthly": monthly, "session": session, "cats": cats,
        "groups": (casa, carro, pets), "user_id": user_id, "ws_id": ws_id,
    }


async def _get(client, auth_headers, **params):
    response = await client.get(URL, params={**SIX, **params}, headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


def _rules(body):
    return {h["rule"]: h for h in body["highlights"]}


@pytest.mark.asyncio
async def test_kpis_match_the_shared_rules(client, auth_headers, world):
    body = await _get(client, auth_headers)
    breakdown = await client.get(
        "/api/reports/breakdown", params={**SIX, "level": "group"}, headers=auth_headers
    )
    assert Decimal(body["kpis"]["spending"]) == Decimal(breakdown.json()["total"]) / 6
    assert body["kpis"]["spending"] == "1000.00"
    assert body["kpis"]["income"] == "5000.00"
    assert body["kpis"]["left"] == "4000.00"
    assert body["kpis"]["left_share"] == 0.8
    assert body["kpis"]["fixed_share"] == 0.0
    assert [m["month"] for m in body["monthly"]] == [f"2026-{m:02d}" for m in range(4, 10)]
    assert body["monthly"][0] == {
        "month": "2026-04", "spending": "1000.00", "income": "5000.00", "median_spending": "1000.00",
    }


@pytest.mark.asyncio
async def test_money_map_left_is_income_minus_spending(client, auth_headers, world):
    world["add"]("Estorno Loja", "mercado", 300, date(2026, 9, 9), "credit")
    world["add"]("Pagamento", None, 120, date(2026, 8, 9), "credit")  # income to check
    await world["session"].commit()
    body = await _get(client, auth_headers)
    money_map = body["money_map"]
    income = sum(Decimal(r["value"]) for r in money_map["income"])
    spending = sum(Decimal(r["value"]) for r in money_map["groups"])
    assert Decimal(money_map["left"]) == income - spending
    assert {"id": None, "label": "A conferir", "value": "120.00"} in money_map["income"]
    assert all(Decimal(r["value"]) > 0 for r in money_map["groups"])
    assert spending == Decimal("5700.00")
    assert money_map["refunds"] == "0.00"
    for group in money_map["groups"]:
        assert sum(Decimal(c["value"]) for c in group["categories"]) == Decimal(group["value"])
    assert money_map["groups"][0]["label"] == "Casa"
    assert money_map["groups"][0]["categories"][0]["label"] == "Mercado"


@pytest.mark.asyncio
async def test_money_map_refund_category_is_eased_out_and_noted(client, auth_headers, world):
    estornos = Category(
        id=uuid.uuid4(), user_id=world["user_id"], workspace_id=world["ws_id"],
        name="Estornos", report_kind="refund",
    )
    world["session"].add(estornos)
    await world["session"].flush()
    world["cats"]["estornos"] = estornos
    world["add"]("Reembolso", "estornos", 250, date(2026, 9, 9), "credit")
    await world["session"].commit()
    money_map = (await _get(client, auth_headers))["money_map"]
    assert money_map["refunds"] == "250.00"
    assert sum(Decimal(g["value"]) for g in money_map["groups"]) == Decimal("5750.00")
    assert Decimal(money_map["left"]) == Decimal("30000.00") - Decimal("5750.00")


@pytest.mark.asyncio
async def test_money_map_left_can_be_negative(client, auth_headers, world):
    world["add"]("Reforma", "combustivel", 40000, date(2026, 9, 20))
    await world["session"].commit()
    assert (await _get(client, auth_headers))["money_map"]["left"] == "-16000.00"


@pytest.mark.asyncio
async def test_spike_fires_above_one_and_a_half_times_the_median(client, auth_headers, world):
    world["add"]("Oficina", "combustivel", 1500, date(2026, 9, 10))
    await world["session"].commit()
    spike = _rules(await _get(client, auth_headers))["spike"]
    assert spike["title"] == "Set/26 custou 2,5 vezes um mês normal"
    assert spike["value"] == "2500.00"
    assert spike["compare"] == "Um mês normal: R$ 1,0 mil"
    assert spike["tone"] == "alert"
    # What pulled the month up, each opening its group in that month.
    carro = str(world["groups"][1].id)
    assert spike["drivers"] == [{
        "label": "Carro", "value": "1500.00",
        "link": {"tab": "explore", "period": "custom", "from": "2026-09-01",
                 "to": "2026-09-30", "group_id": carro},
    }]
    # The whole spending, 12 months up to the period's end.
    assert len(spike["sparkline"]) == 12 and spike["sparkline"][-1] == "2500.00"
    # The link opens the month that spiked, not the whole period.
    month = {"tab": "explore", "period": "custom", "from": "2026-09-01", "to": "2026-09-30"}
    assert spike["link"] == month
    assert spike["actions"] == [{"kind": "month", "label": "Ver Set/26", "link": month}]


@pytest.mark.asyncio
async def test_spike_stays_quiet_below_the_limit(client, auth_headers, world):
    world["add"]("Oficina", "combustivel", 400, date(2026, 9, 10))
    await world["session"].commit()
    assert "spike" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_what_changed_fires_on_a_big_rise(client, auth_headers, world):
    world["monthly"]("Clinica Pet", "vet", [100, 100, 100], first_month=6)
    world["add"]("Clinica Pet", "vet", 700, date(2026, 9, 12))
    await world["session"].commit()
    body = await _get(client, auth_headers)
    card = _rules(body)["what_changed"]
    assert card["title"] == "Pets subiu R$ 600 em Set/26"
    assert card["link"]["group_id"] == str(world["groups"][2].id)
    assert card["tone"] == "alert"
    assert [(d["label"], d["value"]) for d in card["drivers"]] == [("Veterinário", "600.00")]
    assert card["drivers"][0]["link"]["category_id"] == str(world["cats"]["vet"].id)
    assert card["sparkline"][-5:] == ["0.00", "100.00", "100.00", "100.00", "700.00"]
    assert [a["kind"] for a in card["actions"]] == ["explore", "month"]
    assert body["changes"][0]["label"] == "Pets"
    assert body["changes"][0]["delta"] == "600.00"


@pytest.mark.asyncio
async def test_what_changed_stays_quiet_below_the_limit(client, auth_headers, world):
    world["monthly"]("Clinica Pet", "vet", [100, 100, 100], first_month=6)
    world["add"]("Clinica Pet", "vet", 500, date(2026, 9, 12))  # +400
    await world["session"].commit()
    assert "what_changed" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_recurring_new_and_up_share_one_card(client, auth_headers, world):
    world["monthly"]("Spotify", "streaming", [20, 20, 20, 20, 20, 30], first_month=5)
    world["monthly"]("Disney", "streaming", ["34.90", "34.90"], first_month=9)
    await world["session"].commit()
    body = await _get(client, auth_headers)
    card = _rules(body)["recurring_changed"]
    assert card["title"] == "2 gastos fixos começaram ou subiram: R$ 45 a mais por mês"
    assert card["tone"] == "alert"
    assert card["compare"] == "1 começou, 1 subiu"
    assert [(d["label"], d["value"]) for d in card["drivers"]] == [
        ("Disney (começou)", "34.90"), ("Spotify (subiu)", "10.00"),
    ]
    assert card["actions"][0]["kind"] == "recurring"
    assert card["actions"][0]["link"]["tab"] == "recurring"
    assert body["kpis"]["fixed_share"] > 0


@pytest.mark.asyncio
async def test_recurring_new_or_up_stays_quiet_when_steady(client, auth_headers, world):
    world["monthly"]("Spotify", "streaming", [20] * 6)
    await world["session"].commit()
    assert "recurring_changed" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_recurring_expensive_needs_three_thousand_a_year(client, auth_headers, world):
    world["monthly"]("Plano Saude", "streaming", [300] * 6)
    world["monthly"]("Internet", "streaming", [200] * 6)
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["recurring_expensive"]
    assert card["title"] == "Plano Saude custa R$ 3,6 mil por ano"
    assert card["link"]["merchant"] == "PLANO SAUDE"
    assert card["tone"] == "opportunity"
    # The store's charges, 12 closed months up to September.
    assert card["sparkline"][-6:] == ["300.00"] * 6
    assert [a["kind"] for a in card["actions"]] == ["explore", "recurring"]


@pytest.mark.asyncio
async def test_recurring_expensive_stays_quiet_below_the_limit(client, auth_headers, world):
    world["monthly"]("Internet", "streaming", [249] * 6)  # 2.988 a year
    await world["session"].commit()
    assert "recurring_expensive" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_small_purchases_fire_at_a_thousand_a_month(client, auth_headers, world):
    for n in range(26):
        world["add"](f"Padaria {n}", "mercado", 40, date(2026, 10, 1 + n % 20))
    await world["session"].commit()
    body = await _get(client, auth_headers, period="this_month")
    card = _rules(body)["small_purchases"]
    assert card["title"] == "Compras abaixo de R$ 50 somam R$ 1,0 mil por mês"
    assert card["compare"] == "26 compras, R$ 12 mil por ano"
    assert card["value"] == "1040.00"
    assert card["tone"] == "opportunity"
    # "Padaria 1".."Padaria 25" are one store: the numbers are not its name.
    assert [(d["label"], d["value"]) for d in card["drivers"]] == [("Padaria (26×)", "1040.00")]
    assert card["sparkline"][-1] == "1040.00"
    assert card["actions"][0]["link"]["view"] == "merchant"


@pytest.mark.asyncio
async def test_small_purchases_stay_quiet_below_the_limit(client, auth_headers, world):
    for n in range(24):
        world["add"](f"Padaria {n}", "mercado", 40, date(2026, 10, 1 + n % 20))
    await world["session"].commit()
    body = await _get(client, auth_headers, period="this_month")
    assert "small_purchases" not in _rules(body)


@pytest.mark.asyncio
async def test_heavy_group_fires_above_five_percent_and_skips_uncategorized(
    client, auth_headers, world
):
    world["monthly"]("Posto", "combustivel", [300] * 6)  # 1800 of 7800
    world["monthly"]("Sem lugar", None, [900] * 6)
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["heavy_group"]
    assert card["title"] == "Casa custa R$ 12 mil por ano"
    assert card["compare"] == "45% de tudo que você gastou"
    assert card["tone"] == "info"
    assert [(d["label"], d["value"]) for d in card["drivers"]] == [("Mercado", "6000.00")]
    assert card["link"]["group_id"] == str(world["groups"][0].id)


@pytest.mark.asyncio
async def test_heavy_group_stays_quiet_when_every_group_is_small(client, auth_headers, world):
    world["add"]("Reforma", None, 200000, date(2026, 9, 20))  # Casa falls to ~3%
    await world["session"].commit()
    assert "heavy_group" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_to_review_lists_pending_suggestions_and_unclassified_income(
    client, auth_headers, world
):
    tx = world["add"]("Loja Estranha", None, 80, date(2026, 9, 11))
    world["add"]("Pagamento", None, 120, date(2026, 8, 9), "credit")
    # Filed by hand after the question: Revisar no longer lists it.
    stale = world["add"]("Feira", "mercado", 30, date(2026, 9, 12), source="user")
    await world["session"].flush()
    _suggest(world["session"], tx, "LOJA ESTRANHA", category_id=world["cats"]["mercado"].id)
    _suggest(world["session"], stale, "FEIRA", category_id=world["cats"]["mercado"].id)
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["to_review"]
    # The suggested row and the uncategorized credit: what Revisar lists.
    assert card["title"] == "2 itens para revisar"
    assert card["tone"] == "info" and card["sparkline"] is None
    assert card["actions"] == [{"kind": "review", "label": "Revisar", "link": {"path": "/categorization"}}]
    assert "2 transações na Revisar" in card["compare"]
    assert "R$ 120 de renda sem categoria" in card["compare"]


@pytest.mark.asyncio
async def test_to_review_flags_an_unusual_charge_on_a_recurring(client, auth_headers, world):
    world["monthly"]("Spotify", "streaming", [20, 20, 20, 20, 20, 90], first_month=5)
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["to_review"]
    assert "Spotify cobrou R$ 90, fora do normal" in card["compare"]


@pytest.mark.asyncio
async def test_to_review_stays_quiet_with_nothing_to_look_at(client, auth_headers, world):
    assert "to_review" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_at_most_four_cards_and_the_review_card_is_kept(client, auth_headers, world):
    # spike (Sep), what_changed (Pets), new+up, expensive, small, heavy, review.
    world["add"]("Oficina", "combustivel", 1500, date(2026, 9, 10))
    world["monthly"]("Clinica Pet", "vet", [100, 100, 100], first_month=6)
    world["add"]("Clinica Pet", "vet", 700, date(2026, 9, 12))
    world["monthly"]("Plano Saude", "streaming", [300] * 6)
    world["monthly"]("Disney", "streaming", ["34.90", "34.90"], first_month=9)
    for month in range(4, 10):
        for n in range(26):
            world["add"](f"Padaria {n}", "mercado", 40, date(2026, month, 1 + n % 20))
    tx = world["add"]("Loja Estranha", None, 80, date(2026, 9, 11))
    await world["session"].flush()
    _suggest(world["session"], tx, "LOJA ESTRANHA", category_id=world["cats"]["mercado"].id)
    await world["session"].commit()
    body = await _get(client, auth_headers)
    # Disney's October charge also starts the month's pace, which comes
    # first and does not take one of the four places.
    assert body["highlights"][0]["rule"] == "month_pace"
    rules = [h["rule"] for h in body["highlights"][1:]]
    assert len(rules) == 4
    assert rules[-1] == "to_review"
    values = [Decimal(h["value"]) for h in body["highlights"][1:-1]]
    assert values == sorted(values, reverse=True)


@pytest.mark.asyncio
async def test_custom_period_keeps_its_dates_in_the_links(client, auth_headers, world):
    world["add"]("Oficina", "combustivel", 1500, date(2026, 9, 10))
    await world["session"].commit()
    body = await _get(client, auth_headers, period="custom", **{"from": "2026-04-01", "to": "2026-09-30"})
    link = _rules(body)["heavy_group"]["link"]
    assert (link["period"], link["from"], link["to"]) == ("custom", "2026-04-01", "2026-09-30")
    # The spike still opens its own month.
    assert _rules(body)["spike"]["link"] == {
        "tab": "explore", "period": "custom", "from": "2026-09-01", "to": "2026-09-30",
    }


@pytest.mark.asyncio
async def test_money_map_income_is_listed_by_category_not_by_group(client, auth_headers, world):
    session, ws_id, user_id = world["session"], world["ws_id"], world["user_id"]
    renda = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Salário & Renda")
    session.add(renda)
    await session.flush()
    freela = Category(
        id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Freela",
        group_id=renda.id, report_kind="income",
    )
    dividendos = Category(
        id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Dividendos",
        group_id=renda.id, report_kind="income",
    )
    session.add_all([freela, dividendos])
    await session.flush()
    world["cats"].update({"freela": freela, "dividendos": dividendos})
    world["add"]("Cliente", "freela", 700, date(2026, 8, 9), "credit")
    world["add"]("Bolsa", "dividendos", 900, date(2026, 8, 10), "credit")
    await session.commit()
    income = (await _get(client, auth_headers))["money_map"]["income"]
    assert [(r["label"], r["id"], r["value"]) for r in income] == [
        ("Salário", str(world["cats"]["salario"].id), "30000.00"),
        ("Dividendos", str(dividendos.id), "900.00"),
        ("Freela", str(freela.id), "700.00"),
    ]


@pytest.mark.asyncio
async def test_heavy_group_skips_the_generic_other_group(client, auth_headers, world):
    session, ws_id, user_id = world["session"], world["ws_id"], world["user_id"]
    outros = CategoryGroup(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Outros")
    session.add(outros)
    await session.flush()
    misc = Category(
        id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name="Diversos", group_id=outros.id
    )
    session.add(misc)
    await session.flush()
    world["cats"]["misc"] = misc
    world["add"]("Coisas", "misc", 50000, date(2026, 9, 20))  # bigger than every other group
    await session.commit()
    card = _rules(await _get(client, auth_headers))["heavy_group"]
    assert card["title"].startswith("Casa custa")
    assert card["link"]["group_id"] == str(world["groups"][0].id)


@pytest.mark.asyncio
async def test_kpis_previous_are_the_kpis_of_the_period_before(client, auth_headers, world):
    world["add"]("Mercado velho", "mercado", 600, date(2026, 1, 8))
    world["add"]("Salario velho", "salario", 3000, date(2026, 1, 2), "credit")
    await world["session"].commit()
    body = await _get(client, auth_headers)
    # Oct 2025..Mar 2026: 6 months.
    assert body["period"]["previous_from"] == "2025-10-01"
    assert body["kpis_previous"] == {
        "spending": "100.00",
        "income": "500.00",
        "left": "400.00",
        "left_share": 0.8,
        "fixed_share": 0.0,
    }
    separate = await _get(
        client, auth_headers, period="custom",
        **{"from": body["period"]["previous_from"], "to": body["period"]["previous_to"]},
    )
    assert body["kpis_previous"] == separate["kpis"]


@pytest.mark.asyncio
async def test_kpis_previous_is_null_while_the_period_is_partial(client, auth_headers, world):
    body = await _get(client, auth_headers, period="this_month")
    assert body["period"]["partial"] is True
    assert body["kpis_previous"] is None


@pytest.mark.asyncio
async def test_unconverted_count_counts_foreign_rows_without_a_rate(client, auth_headers, world):
    assert (await _get(client, auth_headers))["unconverted_count"] == 0
    tx = world["add"]("Compra fora", "mercado", 50, date(2026, 9, 9))
    tx.currency = "USD"
    await world["session"].commit()
    body = await _get(client, auth_headers)
    assert body["unconverted_count"] == 1
    assert body["kpis"]["spending"] == "1000.00"
