"""GET /api/reports/overview, "O que se repete": the stores you go back to
several times a month (behaviour, not bills), and the categories bought from
most often."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.category import Category
from app.models.category_group import CategoryGroup
from tests.test_reports_overview_api import (  # noqa: F401  (fixtures)
    _fixed_today,
    _get,
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


async def _food(world):  # noqa: F811
    food = CategoryGroup(id=uuid.uuid4(), user_id=world["user_id"], workspace_id=world["ws_id"], name="Alimentação")
    world["session"].add(food)
    await world["session"].flush()
    restaurantes = _category(world, "Restaurantes", food)
    padaria = _category(world, "Padaria", food)
    await world["session"].flush()
    return food, restaurantes, padaria


@pytest.mark.asyncio
async def test_stores_you_go_back_to_most_per_month_with_their_numbers(client, auth_headers, world):  # noqa: F811
    food, restaurantes, _ = await _food(world)
    # Baker Bar: 4 visits in each of Jul, Aug, Sep (12 in the period), R$ 50 each.
    for month in (7, 8, 9):
        for day in (3, 10, 17, 24):
            world["add"]("Baker Bar", "Restaurantes", 50, date(2026, month, day))
    # Recanto: 3 visits, all in one month: not a habit across months.
    for day in (2, 9, 16):
        world["add"]("Recanto", "Restaurantes", 80, date(2026, 9, day))
    await world["session"].commit()

    body = await _get(client, auth_headers)
    keys = [r["merchant"] for r in body["repeats"]]
    assert "BAKER BAR" in keys and "RECANTO" not in keys
    baker = next(r for r in body["repeats"] if r["merchant"] == "BAKER BAR")
    assert baker == {
        "merchant": "BAKER BAR",
        "label": "Baker Bar",
        "category_id": str(restaurantes.id),
        "category": "Restaurantes",
        "group_id": str(food.id),
        "times": 12,
        "months": 3,
        "per_month": 2.0,  # 12 visits over the 6 months of the period
        "month_total": "100.00",
        "year_total": "1200.00",
        "avg_purchase": "50.00",
        "sparkline": ["0.00"] * 9 + ["200.00", "200.00", "200.00"],
    }


@pytest.mark.asyncio
async def test_repeats_are_ordered_by_money_a_month(client, auth_headers, world):  # noqa: F811
    await _food(world)
    for month in (7, 8, 9):
        for day in (3, 10):
            world["add"]("Cafe Pequeno", "Padaria", 10, date(2026, month, day))
            world["add"]("Bistro Caro", "Restaurantes", 200, date(2026, month, day))
    await world["session"].commit()
    keys = [r["merchant"] for r in (await _get(client, auth_headers))["repeats"]]
    assert keys.index("BISTRO CARO") < keys.index("CAFE PEQUENO")


@pytest.mark.asyncio
async def test_bills_fees_and_people_are_not_repeats(client, auth_headers, world):  # noqa: F811
    # A fixed cost, once a month: it is in Fixos, not a habit.
    world["monthly"]("Netflix", "streaming", ["55.90"] * 6)
    # Fees grouped by the store key, and a tax category.
    impostos = _category(world, "Impostos & Taxas")
    await world["session"].flush()
    for month in (7, 8, 9):
        for day in (3, 10):
            world["add"]("Best Buy Iof", None, 12, date(2026, month, day))
            world["add"]("Prefeitura", "Impostos & Taxas", 30, date(2026, month, day))
            world["add"]("Transferência enviada|Joao Silva", None, 40, date(2026, month, day))
    await world["session"].commit()
    assert impostos is not None
    keys = {r["merchant"] for r in (await _get(client, auth_headers))["repeats"]}
    assert keys.isdisjoint({"NETFLIX", "IOF COMPRAS NO EXTERIOR", "PREFEITURA"})
    assert not any(k.startswith("PIX:") for k in keys)


@pytest.mark.asyncio
async def test_at_most_eight_repeats(client, auth_headers, world):  # noqa: F811
    await _food(world)
    for n in range(10):
        for month in (7, 8, 9):
            for day in (3, 10):
                world["add"](f"Loja {chr(65 + n)}", "Restaurantes", 10 + n, date(2026, month, day))
    await world["session"].commit()
    assert len((await _get(client, auth_headers))["repeats"]) == 8


@pytest.mark.asyncio
async def test_the_three_categories_bought_most_often(client, auth_headers, world):  # noqa: F811
    food, restaurantes, padaria = await _food(world)
    for month in range(4, 10):
        for day in range(1, 11):
            world["add"](f"Lugar {day}", "Restaurantes", 30, date(2026, month, day))
        for day in range(1, 5):
            world["add"]("Padoca", "Padaria", 8, date(2026, month, day))
    await world["session"].commit()
    cats = (await _get(client, auth_headers))["repeat_categories"]
    assert [c["category"] for c in cats][:2] == ["Restaurantes", "Padaria"]
    first = cats[0]
    assert first["category_id"] == str(restaurantes.id)
    assert first["group_id"] == str(food.id)
    assert first["times"] == 60
    assert first["per_month"] == 10.0
    assert Decimal(first["month_total"]) == Decimal("300.00")
    assert len(cats) <= 3


@pytest.mark.asyncio
async def test_a_restaurant_you_keep_going_to_enters(client, auth_headers, world):  # noqa: F811
    await _food(world)
    # Baker Bar: 10 visits in 4 months = 2.5 a month where it showed up.
    for month, times in ((6, 3), (7, 2), (8, 3), (9, 2)):
        for day in range(1, times + 1):
            world["add"]("Baker Bar", "Restaurantes", 60, date(2026, month, day))
    await world["session"].commit()
    keys = [r["merchant"] for r in (await _get(client, auth_headers))["repeats"]]
    assert "BAKER BAR" in keys


@pytest.mark.asyncio
async def test_a_rare_expensive_store_is_not_a_habit(client, auth_headers, world):  # noqa: F811
    await _food(world)
    # Wise: 6 purchases in 6 months = 1 a month, and a lot of money.
    for month in range(4, 10):
        world["add"]("Wise", "Restaurantes", 3000, date(2026, month, 5))
    # Booking.com: 5 purchases, 2 a month, but only in 2 months.
    for month in (8, 9):
        for day in (3, 10):
            world["add"]("Booking.com", "Restaurantes", 2000, date(2026, month, day))
    world["add"]("Booking.com", "Restaurantes", 2000, date(2026, 9, 20))
    # A small habit that does qualify, so the list is not empty by accident.
    for month in (7, 8, 9):
        for day in (2, 9):
            world["add"]("Padoca Boa", "Padaria", 20, date(2026, month, day))
    await world["session"].commit()
    keys = [r["merchant"] for r in (await _get(client, auth_headers))["repeats"]]
    assert "WISE" not in keys and "BOOKING.COM" not in keys
    assert "PADOCA BOA" in keys


@pytest.mark.asyncio
async def test_a_store_you_buy_from_all_the_time_enters(client, auth_headers, world):  # noqa: F811
    await _food(world)
    for month in range(4, 10):
        for day in range(1, 19):
            world["add"]("Amazon", "Padaria", 40, date(2026, month, day))
    await world["session"].commit()
    amazon = next(r for r in (await _get(client, auth_headers))["repeats"] if r["merchant"] == "AMAZON")
    assert amazon["times"] == 108
