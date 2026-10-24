"""Fixed costs found in the history: stores that charge about the same, about
once a month.

Rules (simple on purpose, all limits are constants below):

- Rows: debits that count as spending under the shared account rules (same
  filters as `rules.py`: posted, P/L family, reporting date, converted value),
  in categories that are not income/refund. Split shares are not applied: a
  fixed cost is judged by what the card charged.
- Window: the 12 closed months before today plus the current month.
- Installments (`total_installments` set) never count: they are a commitment
  with an end, and Fixos is about subscriptions and bills.
- Cadence is monthly only (one charge in a month). Yearly charges are not
  detected in this version.
- A store is recurring when, in the 6 months ending at its last charge, at
  least 3 distinct months have exactly one charge and the amounts are stable:
  within 20% of their median, ignoring one out-of-line charge.
- A store with 2 consecutive recent months of similar charges and no earlier
  history is also listed, as `new`.
- `PIX:#` (hashed person) and `UNKNOWN` keys are never recurring.
- The person can say a fixed cost is over (`merchant_memory.recurring_ended_on`):
  with no charge after that day the store is `stopped` right away; a later
  charge makes the mark moot and the history decides again.
"""
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import MerchantMemory
from app.models.category import Category
from app.models.transaction import Transaction
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.categorization.merchant_key import HASHED_PERSON_PREFIX, UNKNOWN_KEY
from app.services.reports.merchant_labels import readable_label
from app.services.reports.periods import _shift_months
from app.services.reports.rules import CENT, KIND_NORMAL, ZERO, load_category_kinds

HISTORY_CLOSED_MONTHS = 12
LOOKBACK_MONTHS = 6
MIN_MONTHS_SEEN = 3
#: Largest distance from the median a charge may have and still be "stable".
STABILITY_TOLERANCE = Decimal("0.20")
#: Last charge this far above the earlier median = the price went up.
UP_THRESHOLD = Decimal("0.10")
#: A recent charge this many times the median is not a price change: it is
#: unusual and goes to review.
ATYPICAL_MULTIPLE = Decimal("3")
#: First charge within the current month and the two before = `new`.
NEW_WINDOW_MONTHS = 3

STATUSES = ("new", "up", "stopped", "steady")
NO_CATEGORY_NAME = "Sem categoria"


@dataclass(frozen=True)
class Charge:
    key: str
    category_id: Optional[uuid.UUID]
    day: date
    amount: Decimal


@dataclass
class Recurring:
    merchant_key: str
    category_id: Optional[uuid.UUID]
    amount_month: Decimal
    status: str
    last_date: date
    months_seen: int
    #: How much the last charge rose over the earlier median (status `up`).
    up_share: Optional[Decimal] = None
    #: A recent charge far above the usual one, if any.
    atypical_amount: Optional[Decimal] = None
    #: The day the person marked it as over, while no charge came after it.
    ended_on: Optional[date] = None
    #: The usual charge before the last one and the last one (status `up`).
    previous_amount: Optional[Decimal] = None
    last_amount: Optional[Decimal] = None

    @property
    def amount_year(self) -> Decimal:
        return self.amount_month * 12


def _month_index(value: date) -> int:
    return value.year * 12 + value.month - 1


def is_candidate_key(key: Optional[str]) -> bool:
    return bool(key) and key != UNKNOWN_KEY and not key.startswith(HASHED_PERSON_PREFIX)


def window_start(today: date) -> date:
    return _shift_months(today.replace(day=1), -HISTORY_CLOSED_MONTHS)


async def load_debits(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    primary_currency: str,
    start: date,
    end: date,
) -> list[tuple[Optional[str], Optional[uuid.UUID], date, Decimal, Optional[int]]]:
    """(store key, category id, day, value, installments) of every debit that
    counts as spending between two days. Rows in another currency with no
    converted value stay out, as in the sums."""
    kinds = await load_category_kinds(session, workspace_id)
    mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(mode)
    amount = func.coalesce(Transaction.amount_primary, Transaction.amount)
    rows = await session.execute(
        select(
            Transaction.merchant_key,
            Transaction.category_id,
            report_date,
            amount,
            Transaction.total_installments,
        ).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            Transaction.type == "debit",
            report_date >= start,
            report_date <= end,
            counts_as_user_pnl(),
            (Transaction.currency == primary_currency) | Transaction.amount_primary.is_not(None),
        )
    )
    out = []
    for key, category_id, day, value, installments in rows.all():
        if category_id is not None and kinds.get(category_id, KIND_NORMAL) != KIND_NORMAL:
            continue
        out.append((key, category_id, day, Decimal(value), installments))
    return out


def _within(value: Decimal, center: Decimal) -> bool:
    return center > 0 and abs(value - center) / center <= STABILITY_TOLERANCE


def _stable(amounts: list[Decimal]) -> tuple[bool, Optional[int]]:
    """(stable?, index of the one charge left out of the curve, if any)."""
    center = median(amounts)
    bad = [i for i, value in enumerate(amounts) if not _within(value, center)]
    if not bad:
        return True, None
    if len(bad) == 1 and len(amounts) - 1 >= MIN_MONTHS_SEEN:
        rest = [value for i, value in enumerate(amounts) if i != bad[0]]
        rest_center = median(rest)
        if all(_within(value, rest_center) for value in rest):
            return True, bad[0]
    return False, None


def _classify(
    key: str, charges: list[Charge], today: date, ended_on: Optional[date] = None
) -> Optional[Recurring]:
    current = _month_index(today)
    by_month: dict[int, list[Charge]] = {}
    for charge in charges:
        by_month.setdefault(_month_index(charge.day), []).append(charge)
    first_month, last_month = min(by_month), max(by_month)
    anchor = last_month
    hits = [
        (m, by_month[m][0].amount)
        for m in range(anchor - LOOKBACK_MONTHS + 1, anchor + 1)
        if m in by_month and len(by_month[m]) == 1
    ]
    series = [amount for _, amount in hits]
    atypical: Optional[Decimal] = None
    up_share: Optional[Decimal] = None
    previous_amount: Optional[Decimal] = None

    if len(hits) >= MIN_MONTHS_SEEN:
        ok, outlier = _stable(series)
        if not ok:
            return None
        if outlier is not None:
            rest = [v for i, v in enumerate(series) if i != outlier]
            if series[outlier] >= ATYPICAL_MULTIPLE * median(rest) and hits[outlier][0] >= current - 1:
                atypical = series[outlier]
                series = rest
                hits = [h for i, h in enumerate(hits) if i != outlier]
    else:
        # Fresh store: two consecutive months, nothing before, similar amounts.
        months = sorted(by_month)
        fresh = (
            len(months) >= 2
            and first_month >= current - (NEW_WINDOW_MONTHS - 1)
            and months == list(range(first_month, last_month + 1))
            and all(len(by_month[m]) == 1 for m in months)
            and _stable(series)[0]
        )
        if not fresh:
            return None

    if last_month < current - 1:
        status = "stopped"
    elif first_month >= current - (NEW_WINDOW_MONTHS - 1):
        status = "new"
    else:
        status = "steady"
        if len(series) >= MIN_MONTHS_SEEN and hits[-1][0] >= current - 1:
            earlier = median(series[:-1])
            if earlier > 0 and series[-1] >= earlier * (1 + UP_THRESHOLD):
                status = "up"
                up_share = ((series[-1] - earlier) / earlier).quantize(Decimal("0.001"))
                previous_amount = earlier.quantize(CENT)

    last_date = max(c.day for c in charges)
    marked = ended_on is not None and last_date <= ended_on
    if marked:
        status = "stopped"
    category = Counter(c.category_id for c in charges).most_common(1)[0][0]
    return Recurring(
        merchant_key=key,
        category_id=category,
        amount_month=median(series[-3:]).quantize(CENT),
        status=status,
        last_date=last_date,
        months_seen=len(by_month),
        up_share=up_share if status == "up" else None,
        atypical_amount=atypical.quantize(CENT) if atypical is not None else None,
        ended_on=ended_on if marked else None,
        previous_amount=previous_amount if status == "up" else None,
        last_amount=series[-1].quantize(CENT),
    )


def detect(
    charges: list[Charge], today: date, ended: Optional[dict[str, date]] = None
) -> list[Recurring]:
    """The recurring stores in a list of debits, biggest per year first.
    `ended`: store key -> the day the person marked its fixed cost as over."""
    ended = ended or {}
    per_key: dict[str, list[Charge]] = {}
    for charge in charges:
        if is_candidate_key(charge.key) and charge.amount > ZERO:
            per_key.setdefault(charge.key, []).append(charge)
    found = [
        item
        for key, items in per_key.items()
        if (item := _classify(key, items, today, ended.get(key))) is not None
    ]
    found.sort(key=lambda item: (-item.amount_year, item.merchant_key))
    return found


async def detect_recurring(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    primary_currency: str,
    today: date,
) -> list[Recurring]:
    rows = await load_debits(
        session,
        workspace_id=workspace_id,
        primary_currency=primary_currency,
        start=window_start(today),
        end=today,
    )
    charges = [
        Charge(key, category_id, day, value)
        for key, category_id, day, value, installments in rows
        if installments is None and key is not None
    ]
    return detect(charges, today, await ended_marks(session, workspace_id))


async def ended_marks(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, date]:
    """Store key -> the day the person marked its fixed cost as over."""
    rows = await session.execute(
        select(MerchantMemory.merchant_key, MerchantMemory.recurring_ended_on).where(
            MerchantMemory.workspace_id == workspace_id,
            MerchantMemory.recurring_ended_on.is_not(None),
        )
    )
    return {key: day for key, day in rows.all() if day is not None}


async def mark_ended(
    session: AsyncSession, workspace_id: uuid.UUID, key: str, day: Optional[date]
) -> MerchantMemory | None:
    """Mark (a day) or unmark (None) a store's fixed cost as over. Unmarking a
    store the memory never saw creates nothing and returns None."""
    from app.services.categorization.memory import _get_or_create, stored_key

    key = stored_key(key)
    if day is None:
        row = (
            await session.execute(
                select(MerchantMemory).where(
                    MerchantMemory.workspace_id == workspace_id,
                    MerchantMemory.merchant_key == key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
    else:
        row = await _get_or_create(session, workspace_id, key)
    row.recurring_ended_on = day
    await session.flush()
    return row


async def recurring_report(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    primary_currency: str,
    today: date,
) -> list[dict]:
    items = await detect_recurring(
        session, workspace_id=workspace_id, primary_currency=primary_currency, today=today
    )
    return await to_api(session, workspace_id, items)


async def to_api(
    session: AsyncSession, workspace_id: uuid.UUID, items: list[Recurring]
) -> list[dict]:
    from app.services.reports.breakdown import _store_names

    names = await _store_names(session, workspace_id, {item.merchant_key for item in items})
    category_rows = await session.execute(
        select(Category.id, Category.name).where(Category.workspace_id == workspace_id)
    )
    categories = {cid: name for cid, name in category_rows.all()}
    return [
        {
            "merchant_key": item.merchant_key,
            "label": names.get(item.merchant_key) or readable_label(item.merchant_key),
            "category": categories.get(item.category_id, NO_CATEGORY_NAME),
            "category_id": str(item.category_id) if item.category_id in categories else None,
            "amount_month": f"{item.amount_month:.2f}",
            "amount_year": f"{item.amount_year:.2f}",
            "status": item.status,
            "last_date": item.last_date.isoformat(),
            "months_seen": item.months_seen,
            "ended_on": item.ended_on.isoformat() if item.ended_on else None,
        }
        for item in items
    ]
