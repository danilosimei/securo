"""GET /api/reports/habits: merchants, weekdays, size bands and daily totals."""
import uuid
from datetime import date

import pytest

from app.models.category import Category
from tests.test_categorization_api import _tx

URL = "/api/reports/habits"
SEP = {"period": "custom", "from": "2026-09-01", "to": "2026-09-30"}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr("app.api.reports_breakdown.app_today", lambda: date(2026, 10, 21))


@pytest.fixture
async def world(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    ws_id, user_id = test_workspace.id, test_user.id

    def cat(name, **kw):
        c = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, name=name, **kw)
        session.add(c)
        return c

    compras, estornos, salario = cat("Compras"), cat("Estornos", report_kind="refund"), cat(
        "Salário", report_kind="income"
    )
    await session.flush()
    cats = {"compras": compras, "estornos": estornos, "salario": salario}

    def add(desc, category, amount, day, type="debit", **extra):
        return _tx(
            session, test_user, test_account, desc,
            category_id=cats[category].id if category else None,
            amount=str(amount), type=type, day=day, **extra,
        )

    # 2026-09-01 is a Tuesday.
    add("Amazon", "compras", "10.00", date(2026, 9, 1))      # tue, band 0
    add("Amazon", "compras", "20.00", date(2026, 9, 1))      # tue, band 1 (edge)
    add("Amazon", "compras", "49.99", date(2026, 9, 7))      # mon, band 1
    add("Amazon", "compras", "50.00", date(2026, 9, 7))      # mon, band 2 (edge)
    add("Loja X", "compras", "200.00", date(2026, 9, 6))     # sun, band 3 (edge)
    add("Loja X", "compras", "1000.00", date(2026, 9, 13))   # sun, band 4 (edge)
    add("Amazon", "estornos", "300.00", date(2026, 9, 8), "credit")  # refund: out
    add("Empresa", "salario", "5000.00", date(2026, 9, 5), "credit")  # income: out
    add("Amazon", "compras", "99.00", date(2026, 8, 20))     # outside the period
    await session.commit()
    return {"add": add, "session": session, "cats": cats}


async def _get(client, auth_headers, **params):
    response = await client.get(URL, params={**SEP, **params}, headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_merchants_rank_by_total_with_ticket_and_rhythm(client, auth_headers, world):
    body = await _get(client, auth_headers)
    assert body["merchants"][0] == {
        "category_id": str(world["cats"]["compras"].id),
        "id": "LOJA X", "label": "Loja X", "count": 2, "per_month": 2.0,
        "avg_ticket": "600.00", "total": "1200.00", "category": "Compras",
    }
    amazon = body["merchants"][1]
    assert (amazon["id"], amazon["count"], amazon["total"]) == ("AMAZON", 4, "129.99")
    assert amazon["avg_ticket"] == "32.50"  # 129.99 / 4 = 32.4975
    assert all(m["id"] != "EMPRESA" for m in body["merchants"])


@pytest.mark.asyncio
async def test_weekdays_are_always_seven_monday_first(client, auth_headers, world):
    weekdays = (await _get(client, auth_headers))["weekdays"]
    assert [w["weekday"] for w in weekdays] == list(range(7))
    assert [w["label"] for w in weekdays][:2] == ["seg", "ter"]
    by_day = {w["weekday"]: w for w in weekdays}
    assert (by_day[0]["total"], by_day[0]["count"]) == ("99.99", 2)
    assert (by_day[1]["total"], by_day[1]["count"]) == ("30.00", 2)
    assert (by_day[6]["total"], by_day[6]["count"]) == ("1200.00", 2)
    assert by_day[2] == {"weekday": 2, "label": "qua", "total": "0.00", "count": 0}


@pytest.mark.asyncio
async def test_bands_split_on_the_edges_and_are_always_five(client, auth_headers, world):
    bands = (await _get(client, auth_headers))["bands"]
    assert [(b["min"], b["max"]) for b in bands] == [
        ("0", "20"), ("20", "50"), ("50", "200"), ("200", "1000"), ("1000", None),
    ]
    assert [b["count"] for b in bands] == [1, 2, 1, 1, 1]
    assert bands[1]["total"] == "69.99"


@pytest.mark.asyncio
async def test_days_list_only_days_with_spending(client, auth_headers, world):
    days = (await _get(client, auth_headers))["days"]
    assert days == [
        {"date": "2026-09-01", "total": "30.00"},
        {"date": "2026-09-06", "total": "200.00"},
        {"date": "2026-09-07", "total": "99.99"},
        {"date": "2026-09-13", "total": "1000.00"},
    ]


@pytest.mark.asyncio
async def test_refund_never_counts_as_a_purchase(client, auth_headers, world):
    world["add"]("Amazon", "estornos", "300.00", date(2026, 9, 9))  # even as a debit
    await world["session"].commit()
    body = await _get(client, auth_headers)
    assert sum(b["count"] for b in body["bands"]) == 6
    assert "2026-09-09" not in [d["date"] for d in body["days"]]


@pytest.mark.asyncio
async def test_unnamed_stores_fold_into_one_line_each(client, auth_headers, world):
    rows = [
        (world["add"]("Pix 1", "compras", "30.00", date(2026, 9, 3)), "PIX:#aaa"),
        (world["add"]("Pix 2", "compras", "40.00", date(2026, 9, 4)), "PIX:#bbb"),
        (world["add"]("Sei la", "compras", "5.00", date(2026, 9, 4)), "UNKNOWN"),
    ]
    await world["session"].flush()
    for tx, key in rows:  # keys the hook would derive from a payee; set directly
        tx.merchant_key = key
    await world["session"].commit()
    merchants = {m["id"]: m for m in (await _get(client, auth_headers))["merchants"]}
    assert (merchants["PIX:#"]["label"], merchants["PIX:#"]["count"]) == ("Pix sem nome", 2)
    assert merchants["PIX:#"]["total"] == "70.00"
    assert (merchants["UNKNOWN"]["label"], merchants["UNKNOWN"]["count"]) == ("Sem nome", 1)


@pytest.mark.asyncio
async def test_example_shape(client, auth_headers, world):
    body = await _get(client, auth_headers)
    assert set(body) == {"period", "merchants", "weekdays", "bands", "days"}


@pytest.mark.asyncio
async def test_merchant_without_category_has_null_category_id(client, auth_headers, world):
    world["add"]("Banca", None, "30.00", date(2026, 9, 10))
    await world["session"].commit()
    body = await _get(client, auth_headers)
    banca = next(m for m in body["merchants"] if m["id"] == "BANCA")
    assert banca["category_id"] is None
    assert banca["category"] == "Sem categoria"
