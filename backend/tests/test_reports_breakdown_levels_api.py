"""GET /api/reports/breakdown, levels by category and by store, and the
consistency with the transaction list the screen drills into."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.categorization import MerchantMemory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from tests.test_categorization_api import _tx

URL = "/api/reports/breakdown"
TX_URL = "/api/transactions"
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
    luz = cat("Luz", group_id=casa.id)
    cinema = cat("Cinema", group_id=lazer.id)
    estornos = cat("Estornos", group_id=casa.id, report_kind="refund")
    avulsa = cat("Avulsa")
    salario = cat("Salário", report_kind="income")
    await session.flush()

    def add(desc, category, amount, day, type="debit"):
        return _tx(
            session, test_user, test_account, desc,
            category_id=category.id if category else None,
            amount=amount, type=type, day=day,
        )

    add("Carrefour", mercado, "100.00", date(2026, 9, 5))
    add("Carrefour", mercado, "200.00", date(2026, 9, 12))
    add("Carrefour", mercado, "50.00", date(2026, 9, 20), "credit")  # store refund
    add("Hortifruti Verde", mercado, "30.00", date(2026, 9, 6))
    add("Carrefour", mercado, "40.00", date(2026, 8, 15))  # previous period
    add("Enel", luz, "90.00", date(2026, 9, 7))
    add("Cinemark", cinema, "80.00", date(2026, 9, 8))
    add("Carrefour", estornos, "20.00", date(2026, 9, 9), "credit")
    add("Mercado Livre", avulsa, "5.00", date(2026, 9, 7))
    add("Empresa", salario, "5000.00", date(2026, 9, 5), "credit")
    add("Padaria Estrela", None, "10.00", date(2026, 9, 6))
    add("Padaria Estrela", None, "7.00", date(2026, 9, 16))
    add("Padaria Estrela", None, "3.00", date(2026, 9, 17), "credit")
    await session.commit()
    return {
        "ws_id": ws_id, "casa": casa, "lazer": lazer, "mercado": mercado,
        "luz": luz, "estornos": estornos, "avulsa": avulsa, "salario": salario,
    }


async def _get(client, auth_headers, **params):
    return await client.get(URL, params={**SEP, **params}, headers=auth_headers)


@pytest.mark.asyncio
async def test_category_level_lists_the_categories_of_the_group(client, auth_headers, world):
    response = await _get(client, auth_headers, level="category", group_id=str(world["casa"].id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["label"] for r in body["rows"]] == ["Mercado", "Luz"]
    mercado, luz = body["rows"]
    assert mercado["id"] == str(world["mercado"].id) and mercado["kind"] == "category"
    assert mercado["total"] == "280.00"  # 100 + 200 + 30 - 50
    assert mercado["count"] == 4
    assert mercado["previous_total"] == "40.00"
    assert luz["total"] == "90.00"
    assert len(mercado["sparkline"]) == 12 and mercado["sparkline"][-1] == "280.00"
    assert body["total"] == "370.00" and body["previous_total"] == "40.00"
    # Same number the group shows on the group level (the refund line stays apart).
    group_level = (await _get(client, auth_headers, level="group")).json()
    casa = next(r for r in group_level["rows"] if r["id"] == str(world["casa"].id))
    assert casa["total"] == body["total"]


@pytest.mark.asyncio
async def test_category_level_without_group_is_refused(client, auth_headers, world):
    response = await _get(client, auth_headers, level="category")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "group_required"


@pytest.mark.asyncio
async def test_category_level_of_no_group(client, auth_headers, world):
    body = (await _get(client, auth_headers, level="category", group_id="none")).json()
    assert [r["label"] for r in body["rows"]] == ["Avulsa"]
    assert body["total"] == "5.00"


@pytest.mark.asyncio
async def test_merchant_level_sums_stores_and_nets_refunds(client, auth_headers, world):
    response = await _get(
        client, auth_headers, level="merchant", category_id=str(world["mercado"].id)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["label"] for r in body["rows"]] == ["Carrefour", "Hortifruti Verde"]
    carrefour, pao = body["rows"]
    assert carrefour["id"] == "CARREFOUR" and carrefour["kind"] == "merchant"
    assert carrefour["total"] == "250.00"  # 100 + 200 - 50
    assert carrefour["count"] == 3
    assert carrefour["previous_total"] == "40.00"
    assert carrefour["sparkline"][-1] == "250.00" and len(carrefour["sparkline"]) == 12
    assert pao["total"] == "30.00"
    assert body["total"] == "280.00"
    # Another category's store does not leak in.
    assert "Enel" not in [r["label"] for r in body["rows"]]


@pytest.mark.asyncio
async def test_merchant_level_uncategorized(client, auth_headers, world):
    body = (await _get(client, auth_headers, level="merchant", category_id="none")).json()
    assert [r["label"] for r in body["rows"]] == ["Padaria Estrela"]
    assert body["rows"][0]["total"] == "17.00"  # debits only, as the group level does


@pytest.mark.asyncio
async def test_merchant_level_refund_category_is_negative(client, auth_headers, world):
    body = (
        await _get(client, auth_headers, level="merchant", category_id=str(world["estornos"].id))
    ).json()
    assert body["rows"][0]["total"] == "-20.00"


@pytest.mark.asyncio
async def test_merchant_level_without_category_is_refused(client, auth_headers, world):
    response = await _get(client, auth_headers, level="merchant")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "category_required"


@pytest.mark.asyncio
async def test_merchant_label_prefers_the_clean_name_in_memory(
    session, client, auth_headers, world
):
    session.add(
        MerchantMemory(
            id=uuid.uuid4(), workspace_id=world["ws_id"], merchant_key="CARREFOUR",
            counts={}, ai_clean_name="Carrefour Hiper",
        )
    )
    await session.commit()
    body = (
        await _get(client, auth_headers, level="merchant", category_id=str(world["mercado"].id))
    ).json()
    assert body["rows"][0]["label"] == "Carrefour Hiper"
    assert body["rows"][0]["id"] == "CARREFOUR"


@pytest.mark.asyncio
async def test_merchant_total_matches_the_transaction_list(client, auth_headers, world):
    body = (
        await _get(client, auth_headers, level="merchant", category_id=str(world["mercado"].id))
    ).json()
    for row in body["rows"]:
        listing = await client.get(
            TX_URL,
            params={
                "category_id": str(world["mercado"].id), "merchant_key": row["id"],
                "from": "2026-09-01", "to": "2026-09-30", "status": "posted",
                "user_pnl_only": "true", "page": 1, "limit": 50,
            },
            headers=auth_headers,
        )
        assert listing.status_code == 200, listing.text
        data = listing.json()
        assert data["total"] == row["count"]
        net = Decimal(str(data["summary"]["expense"])) - Decimal(str(data["summary"]["income"]))
        assert net == Decimal(row["total"])


@pytest.mark.asyncio
async def test_uncategorized_merchant_matches_the_transaction_list(client, auth_headers, world):
    listing = await client.get(
        TX_URL,
        params={
            "uncategorized": "true", "merchant_key": "PADARIA ESTRELA",
            "from": "2026-09-01", "to": "2026-09-30", "status": "posted",
            "user_pnl_only": "true",
        },
        headers=auth_headers,
    )
    data = listing.json()
    assert data["total"] == 3
    assert Decimal(str(data["summary"]["expense"])) == Decimal("17.00")


@pytest.mark.asyncio
async def test_merchant_key_filter_is_exact(client, auth_headers, world):
    listing = await client.get(
        TX_URL, params={"merchant_key": "CARREFOUR", "limit": 100}, headers=auth_headers
    )
    assert listing.json()["total"] == 5
    listing = await client.get(
        TX_URL, params={"merchant_key": "CARREF", "limit": 100}, headers=auth_headers
    )
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_scope_id_must_be_uuid_or_none(client, auth_headers, world):
    response = await _get(client, auth_headers, level="category", group_id="casa")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_id"


@pytest.mark.asyncio
async def test_all_groups_lists_every_spending_category_with_its_group(
    client, auth_headers, world
):
    response = await _get(client, auth_headers, level="category", group_id="all")
    assert response.status_code == 200, response.text
    rows = {row["label"]: row for row in response.json()["rows"]}
    assert set(rows) == {"Mercado", "Luz", "Cinema", "Avulsa"}  # no income, no refund
    assert rows["Mercado"]["group_label"] == "Casa"
    assert rows["Cinema"]["group_label"] == "Lazer"
    assert rows["Avulsa"]["group_label"] == "Sem grupo"
    assert len(rows["Mercado"]["sparkline"]) == 12
    assert rows["Mercado"]["total"] == "280.00"


@pytest.mark.asyncio
async def test_group_label_is_null_outside_the_all_groups_listing(client, auth_headers, world):
    response = await _get(client, auth_headers, level="category", group_id=str(world["casa"].id))
    assert all(row["group_label"] is None for row in response.json()["rows"])
