"""The account rules every report shares: which rows count and how they add up.

One base query so the Explorar tab, the overview and the dashboard agree by
construction.

- Rows: posted, inside the P/L family (`counts_as_user_pnl`: no paired
  transfers, ignored rows, `exclude_from_pnl`, transfer-like or ignored
  categories, settlements), bucketed by the same reporting date column the
  older income/expenses report uses.
- Value: `coalesce(amount_primary, amount)`. A row in another currency with no
  `amount_primary` has no trustworthy value: it stays out of the sums and is
  counted in `unconverted_count` so the screen can warn.
- Spending of a `normal` or `refund` category = debits - credits (a refund
  credit eases the spending; a `refund` category ends up negative). Income of
  an `income` category = credits - debits. Without a category, debits are
  spending and credits are "income to check" (`uncategorized_income`).
- Splits: only the viewer's share counts, as in the budget (non-owner shares
  of own splits are subtracted, the viewer's share of other people's group
  splits is added), through the same helpers.
"""
import uuid
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.group import GroupMember
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.services._query_filters import (
    counts_as_user_pnl,
    owner_split_offset_by_category,
    reporting_date_col,
    viewer_shared_spending_by_category,
)
from app.services.admin_service import get_credit_card_accounting_mode

ZERO = Decimal("0")
CENT = Decimal("0.01")

#: report_kind values (closed list, CHECK on the column).
KIND_NORMAL = "normal"
KIND_INCOME = "income"
KIND_REFUND = "refund"

Segment = tuple[date, date]  # first and last day, both inclusive


@dataclass
class Flow:
    """Debits and credits of one category inside one segment."""

    debit: Decimal = ZERO
    credit: Decimal = ZERO
    count: int = 0


@dataclass
class SegmentFlows:
    by_category: dict[Optional[uuid.UUID], Flow] = field(default_factory=dict)
    unconverted_count: int = 0

    def flow(self, category_id: Optional[uuid.UUID]) -> Flow:
        return self.by_category.setdefault(category_id, Flow())


def spend_of(flow: Flow, kind: Optional[str]) -> Decimal:
    """Spending a category's flow adds (0 for income categories)."""
    if kind == KIND_INCOME:
        return ZERO
    return flow.debit - flow.credit


def income_of(flow: Flow, kind: Optional[str]) -> Decimal:
    """Income a category's flow adds (0 unless the category is `income`)."""
    if kind == KIND_INCOME:
        return flow.credit - flow.debit
    return ZERO


async def load_category_kinds(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    rows = await session.execute(
        select(Category.id, Category.report_kind).where(Category.workspace_id == workspace_id)
    )
    return {cid: kind for cid, kind in rows.all()}


async def collect_flows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    segments: list[Segment],
) -> list[SegmentFlows]:
    """Debits and credits per category for each (disjoint) segment, one pass
    over the rows. Callers choose the segments: a period, its comparison
    period and the monthly slices of a sparkline are all just segments."""
    out = [SegmentFlows() for _ in segments]
    if not segments:
        return out

    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)
    lo = min(start for start, _ in segments)
    hi = max(end for _, end in segments)
    amount = func.coalesce(Transaction.amount_primary, Transaction.amount)
    unconverted = case(
        (
            (Transaction.currency != primary_currency)
            & Transaction.amount_primary.is_(None),
            True,
        ),
        else_=False,
    )
    day = report_date.label("day")
    rows = await session.execute(
        select(
            Transaction.category_id,
            Transaction.type,
            unconverted.label("unconverted"),
            day,
            func.sum(amount),
            func.count(Transaction.id),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            report_date >= lo,
            report_date <= hi,
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id, Transaction.type, unconverted, report_date)
    )

    order = sorted(range(len(segments)), key=lambda i: segments[i][0])
    starts = [segments[i][0] for i in order]
    for category_id, tx_type, is_unconverted, row_day, total, count in rows.all():
        pos = bisect_right(starts, row_day) - 1
        if pos < 0:
            continue
        index = order[pos]
        if row_day > segments[index][1]:
            continue
        if is_unconverted:
            out[index].unconverted_count += count
            continue
        flow = out[index].flow(category_id)
        value = Decimal(total or 0)
        if tx_type == "credit":
            flow.credit += value
        else:
            flow.debit += value
        flow.count += count

    await _apply_split_offsets(session, workspace_id, user_id, primary_currency, segments, out)
    return out


async def _apply_split_offsets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    segments: list[Segment],
    out: list[SegmentFlows],
) -> None:
    has_own = (
        await session.execute(
            select(TransactionSplit.id).where(TransactionSplit.workspace_id == workspace_id).limit(1)
        )
    ).first() is not None
    has_shared = (
        await session.execute(
            select(GroupMember.id)
            .where(GroupMember.linked_user_id == user_id, GroupMember.is_self.is_(False))
            .limit(1)
        )
    ).first() is not None
    if not (has_own or has_shared):
        return
    accounting_mode = await get_credit_card_accounting_mode(session)
    accrual = accounting_mode == "accrual"
    for (start, end), seg in zip(segments, out):
        stop = end + timedelta(days=1)  # the helpers take an exclusive end
        if has_own:
            offsets = await owner_split_offset_by_category(
                session, user_id, start, stop, use_effective_date=accrual,
                primary_currency=primary_currency, workspace_id=workspace_id,
            )
            for category_id, value in offsets.items():
                seg.flow(category_id).debit -= Decimal(str(value))
        if has_shared:
            shared = await viewer_shared_spending_by_category(
                session, user_id, start, stop, use_effective_date=accrual,
                primary_currency=primary_currency,
            )
            for category_id, value in shared.items():
                seg.flow(category_id).debit += Decimal(str(value))


@dataclass
class RangeTotals:
    """Spending and income of one range, per category."""

    spend_by_category: dict[Optional[uuid.UUID], Decimal]
    count_by_category: dict[Optional[uuid.UUID], int]
    income_by_category: dict[uuid.UUID, Decimal]
    #: Credits without a category: income that still needs a look.
    uncategorized_income: Decimal
    unconverted_count: int

    @property
    def spending(self) -> Decimal:
        return sum(self.spend_by_category.values(), ZERO)

    @property
    def income(self) -> Decimal:
        return sum(self.income_by_category.values(), ZERO)


def totals_from(
    segments: list[SegmentFlows], kinds: dict[uuid.UUID, str]
) -> RangeTotals:
    """Fold one or more consecutive segments into the totals of their range."""
    spend: dict[Optional[uuid.UUID], Decimal] = {}
    count: dict[Optional[uuid.UUID], int] = {}
    income: dict[uuid.UUID, Decimal] = {}
    uncategorized_income = ZERO
    unconverted = 0
    for seg in segments:
        unconverted += seg.unconverted_count
        for category_id, flow in seg.by_category.items():
            if category_id is None:
                spend[None] = spend.get(None, ZERO) + flow.debit
                uncategorized_income += flow.credit
                count[None] = count.get(None, 0) + flow.count
                continue
            kind = kinds.get(category_id, KIND_NORMAL)
            if kind == KIND_INCOME:
                income[category_id] = income.get(category_id, ZERO) + income_of(flow, kind)
            else:
                spend[category_id] = spend.get(category_id, ZERO) + spend_of(flow, kind)
            count[category_id] = count.get(category_id, 0) + flow.count
    return RangeTotals(
        spend_by_category={k: v.quantize(CENT) for k, v in spend.items()},
        count_by_category=count,
        income_by_category={k: v.quantize(CENT) for k, v in income.items()},
        uncategorized_income=uncategorized_income.quantize(CENT),
        unconverted_count=unconverted,
    )


async def range_totals(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
) -> RangeTotals:
    """Spending and income per category between two days (inclusive)."""
    flows = await collect_flows(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        primary_currency=primary_currency,
        segments=[(start, end)],
    )
    return totals_from(flows, await load_category_kinds(session, workspace_id))
