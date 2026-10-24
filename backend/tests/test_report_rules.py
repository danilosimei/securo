"""Account rules shared by the new reports (spending, income, exclusions)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.category import Category
from app.services.reports.rules import range_totals
from tests.test_categorization_api import _tx

START, END = date(2026, 9, 1), date(2026, 9, 30)
DAY = date(2026, 9, 10)


@pytest.fixture
async def ctx(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    await session.commit()
    return {
        "user": test_user,
        "account": test_account,
        "ws_id": test_workspace.id,
        "user_id": test_user.id,
    }


def _cat(session, ctx, name, **kw):
    cat = Category(
        id=uuid.uuid4(), user_id=ctx["user_id"], workspace_id=ctx["ws_id"], name=name, **kw
    )
    session.add(cat)
    return cat


def _add(session, ctx, cat, amount, *, type="debit", day=DAY, **extra):
    currency = extra.pop("currency", "BRL")
    tx = _tx(
        session, ctx["user"], ctx["account"], f"{type} {amount}",
        category_id=cat.id if cat is not None else None,
        amount=str(amount), type=type, day=day, **extra,
    )
    tx.currency = currency  # `_tx` fixes BRL
    return tx


async def _totals(session, ctx):
    await session.commit()
    return await range_totals(
        session, workspace_id=ctx["ws_id"], user_id=ctx["user_id"],
        primary_currency="BRL", start=START, end=END,
    )


@pytest.mark.asyncio
async def test_refund_in_the_same_category_eases_the_spending(session, ctx):
    shop = _cat(session, ctx, "Compras")
    await session.flush()
    _add(session, ctx, shop, "100.00")
    _add(session, ctx, shop, "30.00", type="credit")
    totals = await _totals(session, ctx)
    assert totals.spend_by_category[shop.id] == Decimal("70.00")
    assert totals.income == Decimal("0")
    assert totals.count_by_category[shop.id] == 2


@pytest.mark.asyncio
async def test_refund_category_is_negative_and_lowers_the_total(session, ctx):
    shop = _cat(session, ctx, "Compras")
    refunds = _cat(session, ctx, "Estornos & Reembolsos", report_kind="refund")
    await session.flush()
    _add(session, ctx, shop, "100.00")
    _add(session, ctx, refunds, "25.00", type="credit")
    totals = await _totals(session, ctx)
    assert totals.spend_by_category[refunds.id] == Decimal("-25.00")
    assert totals.spending == Decimal("75.00")
    assert totals.income == Decimal("0")


@pytest.mark.asyncio
async def test_credit_in_income_category_is_income_not_spending(session, ctx):
    salary = _cat(session, ctx, "Salário", report_kind="income")
    await session.flush()
    _add(session, ctx, salary, "5000.00", type="credit")
    totals = await _totals(session, ctx)
    assert totals.income_by_category[salary.id] == Decimal("5000.00")
    assert totals.income == Decimal("5000.00")
    assert totals.spending == Decimal("0")
    assert salary.id not in totals.spend_by_category


@pytest.mark.asyncio
async def test_without_category_debits_spend_and_credits_wait_for_review(session, ctx):
    _add(session, ctx, None, "40.00")
    _add(session, ctx, None, "15.00", type="credit")
    totals = await _totals(session, ctx)
    assert totals.spend_by_category[None] == Decimal("40.00")
    assert totals.uncategorized_income == Decimal("15.00")
    assert totals.income == Decimal("0")


@pytest.mark.asyncio
async def test_transfers_ignored_and_pending_rows_stay_out(session, ctx):
    shop = _cat(session, ctx, "Compras")
    invest = _cat(session, ctx, "Investimentos", treat_as_transfer=True)
    hidden = _cat(session, ctx, "Ocultas", is_ignored=True)
    await session.flush()
    _add(session, ctx, shop, "10.00")  # counts
    _add(session, ctx, shop, "500.00", transfer_pair_id=uuid.uuid4())
    _add(session, ctx, invest, "700.00")
    _add(session, ctx, hidden, "800.00")
    _add(session, ctx, shop, "900.00", is_ignored=True)
    _add(session, ctx, shop, "950.00", exclude_from_pnl=True)
    _add(session, ctx, shop, "600.00", status="pending")
    totals = await _totals(session, ctx)
    assert totals.spending == Decimal("10.00")
    assert set(totals.spend_by_category) == {shop.id}


@pytest.mark.asyncio
async def test_rows_outside_the_range_stay_out(session, ctx):
    shop = _cat(session, ctx, "Compras")
    await session.flush()
    _add(session, ctx, shop, "10.00", day=date(2026, 8, 31))
    _add(session, ctx, shop, "20.00", day=date(2026, 10, 1))
    _add(session, ctx, shop, "30.00", day=END)
    totals = await _totals(session, ctx)
    assert totals.spending == Decimal("30.00")


@pytest.mark.asyncio
async def test_foreign_currency_without_amount_primary_is_counted_not_summed(session, ctx):
    shop = _cat(session, ctx, "Compras")
    await session.flush()
    _add(session, ctx, shop, "10.00")
    _add(session, ctx, shop, "99.00", currency="USD")
    _add(session, ctx, shop, "99.00", currency="USD")
    totals = await _totals(session, ctx)
    assert totals.spending == Decimal("10.00")
    assert totals.unconverted_count == 2


@pytest.mark.asyncio
async def test_amount_primary_is_preferred_when_present(session, ctx):
    shop = _cat(session, ctx, "Compras")
    await session.flush()
    _add(session, ctx, shop, "20.00", currency="USD", amount_primary=Decimal("110.00"))
    totals = await _totals(session, ctx)
    assert totals.spending == Decimal("110.00")
    assert totals.unconverted_count == 0


@pytest.mark.asyncio
async def test_only_the_users_share_of_a_split_expense_counts(session, ctx):
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    dinner = _cat(session, ctx, "Jantar")
    group = Group(
        id=uuid.uuid4(), user_id=ctx["user_id"], workspace_id=ctx["ws_id"],
        name="Viagem", kind="shared", default_currency="BRL",
    )
    session.add(group)
    await session.flush()
    friend = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=ctx["ws_id"], name="Amigo", is_self=False
    )
    session.add(friend)
    tx = _add(session, ctx, dinner, "1000.00")
    await session.flush()
    session.add(TransactionSplit(
        id=uuid.uuid4(), transaction_id=tx.id, workspace_id=ctx["ws_id"],
        group_member_id=friend.id, share_amount=Decimal("400"), share_type="amount",
    ))
    totals = await _totals(session, ctx)
    assert totals.spend_by_category[dinner.id] == Decimal("600.00")
