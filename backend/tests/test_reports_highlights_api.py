"""GET /api/reports/overview, the highlights added for the cards: how the
month is going, fixed costs that started, rose or ended, and the place
bought from most often."""
import uuid
from datetime import date

import pytest

from app.models.category import Category
from app.models.category_group import CategoryGroup
from tests.test_reports_overview_api import (  # noqa: F401  (fixtures)
    _fixed_today,
    _get,
    _rules,
    world,
)


def _category(world, name, group=None, **kw):  # noqa: F811
    category = Category(
        id=uuid.uuid4(), user_id=world["user_id"], workspace_id=world["ws_id"], name=name,
        group_id=group.id if group else None, **kw,
    )
    world["session"].add(category)
    world["cats"][name] = category
    return category


# month_pace: today is 2026-10-21; Apr..Sep spent 1000 each (a normal month),
# so a normal October by the 21st is 1000 × 21 / 31 = 677.42.


@pytest.mark.asyncio
async def test_month_pace_above_normal_comes_first(client, auth_headers, world):  # noqa: F811
    world["add"]("Oficina", "combustivel", 1500, date(2026, 10, 3))
    await world["session"].commit()
    body = await _get(client, auth_headers)
    pace = body["highlights"][0]
    assert pace["rule"] == "month_pace"
    assert pace["title"] == "Em 21 dias você gastou R$ 1,5 mil, 121% acima do normal"
    assert pace["compare"] == "Nesse passo, o mês fecha em R$ 2,2 mil"
    assert pace["tone"] == "alert"
    assert pace["value"] == "1500.00"
    assert [(d["label"], d["value"]) for d in pace["drivers"]] == [
        ("Normal até hoje", "677.42"),
        ("Nesse passo, fecha em", "2214.29"),
        ("Um mês normal", "1000.00"),
    ]
    assert pace["sparkline"] is None
    assert pace["actions"][0]["kind"] == "month"
    assert pace["actions"][0]["link"] == {"tab": "explore", "period": "this_month"}


@pytest.mark.asyncio
async def test_month_pace_below_normal_is_good_news(client, auth_headers, world):  # noqa: F811
    world["add"]("Oficina", "combustivel", 300, date(2026, 10, 3))
    await world["session"].commit()
    pace = _rules(await _get(client, auth_headers))["month_pace"]
    assert pace["tone"] == "opportunity"
    assert pace["title"] == "Em 21 dias você gastou R$ 300, 56% abaixo do normal"


@pytest.mark.asyncio
async def test_month_pace_is_about_this_month_whatever_the_period(client, auth_headers, world):  # noqa: F811
    world["add"]("Oficina", "combustivel", 1500, date(2026, 10, 3))
    await world["session"].commit()
    for period in ("last_3m", "this_month"):
        assert _rules(await _get(client, auth_headers, period=period))["month_pace"]["value"] == "1500.00"


@pytest.mark.asyncio
async def test_month_pace_stays_quiet_close_to_normal(client, auth_headers, world):  # noqa: F811
    world["add"]("Oficina", "combustivel", 700, date(2026, 10, 3))  # +3%
    await world["session"].commit()
    assert "month_pace" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_month_pace_waits_for_the_fifth_day(client, auth_headers, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.api.reports_breakdown.app_today", lambda: date(2026, 10, 4))
    world["add"]("Oficina", "combustivel", 1500, date(2026, 10, 2))
    await world["session"].commit()
    assert "month_pace" not in _rules(await _get(client, auth_headers))


# recurring_changed: what ended is good news.


@pytest.mark.asyncio
async def test_a_fixed_cost_that_ended_is_good_news(client, auth_headers, world):  # noqa: F811
    _category(world, "Financiamento do veículo", world["groups"][1])
    await world["session"].flush()
    world["monthly"]("Stellantis", "Financiamento do veículo", [4265] * 4, day=2)  # Apr..Jul
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["recurring_changed"]
    assert card["title"] == (
        "Financiamento do veículo (Stellantis) acabou: R$ 4,3 mil por mês a menos desde agosto"
    )
    assert card["tone"] == "opportunity"
    assert card["compare"] == "R$ 51 mil por ano a menos"
    assert [(d["label"], d["value"]) for d in card["drivers"]] == [
        ("Antes", "4265.00"), ("Agora", "0.00"),
    ]
    # 12 months up to October: the charges, then nothing.
    assert card["sparkline"][-7:] == ["4265.00"] * 4 + ["0.00"] * 3
    assert [a["kind"] for a in card["actions"]] == ["recurring", "explore"]


@pytest.mark.asyncio
async def test_a_charge_due_this_month_is_not_over_yet(client, auth_headers, world):  # noqa: F811
    _category(world, "Financiamento do veículo", world["groups"][1])
    await world["session"].flush()
    world["monthly"]("Stellantis", "Financiamento do veículo", [4265] * 6, day=2)  # Apr..Sep
    await world["session"].commit()
    rules = _rules(await _get(client, auth_headers))
    assert "recurring_changed" not in rules
    assert rules["recurring_expensive"]["title"] == "Stellantis custa R$ 51 mil por ano"


@pytest.mark.asyncio
async def test_a_fixed_cost_that_ended_long_ago_is_old_news(client, auth_headers, world):  # noqa: F811
    world["monthly"]("Academia", "streaming", [90] * 3, day=2)  # Apr..Jun
    await world["session"].commit()
    assert "recurring_changed" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_marked_as_over_counts_as_ended(client, auth_headers, world):  # noqa: F811
    world["monthly"]("Netflix", "streaming", ["55.90"] * 6)  # Apr..Sep
    await world["session"].commit()
    assert (await client.post("/api/reports/recurring/NETFLIX/end", headers=auth_headers)).status_code == 200
    card = _rules(await _get(client, auth_headers))["recurring_changed"]
    assert card["tone"] == "opportunity"
    assert card["title"] == "Streaming (Netflix) acabou: R$ 56 por mês a menos desde outubro"


# frequent_spot: the category bought from most often.


def _visits(world, category, stores, months=range(4, 10), amount=60):  # noqa: F811
    for month in months:
        day = 1
        for store, times in stores:
            for _ in range(times):
                world["add"](store, category, amount, date(2026, month, day))
                day += 1


@pytest.mark.asyncio
async def test_frequent_spot_names_the_category_and_its_places(client, auth_headers, world):  # noqa: F811
    food = CategoryGroup(id=uuid.uuid4(), user_id=world["user_id"], workspace_id=world["ws_id"], name="Alimentação")
    world["session"].add(food)
    await world["session"].flush()
    restaurantes = _category(world, "Restaurantes", food)
    await world["session"].flush()
    _visits(world, "Restaurantes", [("Baker Bar", 4), ("Recanto Paraibano", 3), ("Real Botequim", 2), ("Outro", 1)])
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["frequent_spot"]
    assert card["title"] == "Restaurantes: R$ 600 por mês, cerca de 10 vezes por mês"
    assert card["tone"] == "info"
    assert [(d["label"], d["value"]) for d in card["drivers"]] == [
        ("Baker Bar (24×)", "1440.00"),
        ("Recanto Paraibano (18×)", "1080.00"),
        ("Real Botequim (12×)", "720.00"),
    ]
    assert card["drivers"][0]["link"]["merchant"] == "BAKER BAR"
    assert card["drivers"][0]["link"]["category_id"] == str(restaurantes.id)
    assert card["actions"][0] == {
        "kind": "explore", "label": "Ver no Explorar",
        "link": {"tab": "explore", "period": "last_6m", "category_id": str(restaurantes.id)},
    }
    assert card["sparkline"][-6:] == ["600.00"] * 6


@pytest.mark.asyncio
async def test_frequent_spot_stays_quiet_below_the_limits(client, auth_headers, world):  # noqa: F811
    _category(world, "Restaurantes")
    await world["session"].flush()
    _visits(world, "Restaurantes", [("Baker Bar", 7)], amount=80)  # 7 times, 560 a month
    _category(world, "Lanches")
    await world["session"].flush()
    _visits(world, "Lanches", [("Padoca", 9)], amount=40)  # 9 times, 360 a month
    await world["session"].commit()
    assert "frequent_spot" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_frequent_spot_skips_the_generic_other_group_categories(client, auth_headers, world):  # noqa: F811
    # "Compras" and "Outros" are seeded under the generic "Other" group: they
    # buy often by nature, which says nothing about a habit.
    for name in ("Compras", "Outros", "Restaurantes"):
        _category(world, name)
    await world["session"].flush()
    _visits(world, "Compras", [("Amazon", 25)], amount=60)
    _visits(world, "Outros", [("Diversos", 22)], amount=60)
    _visits(world, "Restaurantes", [("Baker Bar", 20)], amount=60)
    await world["session"].commit()
    card = _rules(await _get(client, auth_headers))["frequent_spot"]
    assert card["title"].startswith("Restaurantes:")
    assert card["drivers"][0]["label"] == "Baker Bar (120×)"


@pytest.mark.asyncio
async def test_frequent_spot_stays_quiet_when_only_generic_categories_repeat(client, auth_headers, world):  # noqa: F811
    _category(world, "Compras")
    await world["session"].flush()
    _visits(world, "Compras", [("Amazon", 25)], amount=60)
    await world["session"].commit()
    assert "frequent_spot" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_frequent_spot_skips_taxes_and_fees(client, auth_headers, world):  # noqa: F811
    _category(world, "Impostos & Taxas")
    await world["session"].flush()
    _visits(world, "Impostos & Taxas", [("IOF Compras no Exterior", 12)])
    await world["session"].commit()
    assert "frequent_spot" not in _rules(await _get(client, auth_headers))


@pytest.mark.asyncio
async def test_frequent_spot_always_gets_one_of_the_four_places(client, auth_headers, world, monkeypatch):  # noqa: F811
    from app.services.reports import highlights

    def big(rule, impact):
        async def rule_fn(ctx, cards):
            return highlights.card(rule, rule, 1, "", tone="info", actions=[], impact=impact)

        return rule_fn

    def big_sync(rule, impact):
        return lambda ctx, cards: highlights.card(rule, rule, 1, "", tone="info", actions=[], impact=impact)

    monkeypatch.setattr(highlights, "_spike", big_sync("spike", 90000))
    monkeypatch.setattr(highlights, "_heavy_group", big_sync("heavy_group", 80000))
    monkeypatch.setattr(highlights, "_what_changed", big_sync("what_changed", 5000))
    monkeypatch.setattr(highlights, "_recurring_expensive", big("recurring_expensive", 70000))
    monkeypatch.setattr(highlights, "_to_review", big("to_review", 1))
    _category(world, "Restaurantes")
    await world["session"].flush()
    _visits(world, "Restaurantes", [("Baker Bar", 10)])  # R$ 600 a month: small impact
    await world["session"].commit()
    rules = [h["rule"] for h in (await _get(client, auth_headers))["highlights"]]
    assert "frequent_spot" in rules
    assert "to_review" in rules
    assert "what_changed" not in rules  # the lowest impact among the others gave way
    assert len(rules) <= highlights.MAX_HIGHLIGHTS + 1
