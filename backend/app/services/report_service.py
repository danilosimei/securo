import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.user import User
from app.services._query_filters import (
    counts_as_pnl,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.account_service import get_account_name
from app.services.fx_rate_service import convert
from app.schemas.report import (
    ReportBreakdown,
    ReportCompositionItem,
    ReportDataPoint,
    ReportMeta,
    ReportResponse,
    ReportSummary,
)
from app.services import invoice_forecast_service
from app.services.dashboard_service import (
    _account_balance_at,
    _counts_as_user_pnl_row,
    _get_forecast_transactions,
    _get_open_accounts,
)

CATEGORY_TREND_TOP_N = 11

_ACCOUNT_TYPE_COLORS: dict[str, str] = {
    "checking": "#6366F1",
    "savings": "#3B82F6",
    "credit_card": "#F43F5E",
    "investment": "#8B5CF6",
    "wallet": "#F59E0B",
}
_ASSET_TYPE_COLORS: dict[str, str] = {
    "real_estate": "#0EA5E9",
    "vehicle": "#14B8A6",
    "valuable": "#F59E0B",
    "investment": "#8B5CF6",
    "other": "#6B7280",
}


def _report_start_date(
    today: date,
    months: int,
    period: str | None = None,
    days: int | None = None,
    financial_year_start_month: int = 1,
    start_date: date | None = None,
) -> date:
    """Resolve historical report start date.

    An explicit ``start_date`` wins over every other selector so a caller can
    pin the window to a calendar range. `days` requests an exact rolling window
    ending today (inclusive), instead of the month-aligned window the `months`
    ranges use.
    """
    if start_date is not None:
        return start_date

    if days:
        return today - timedelta(days=days - 1)

    if period == "ytd":
        if not 1 <= financial_year_start_month <= 12:
            raise ValueError("financial_year_start_month must be between 1 and 12")
        year = today.year - (today.month < financial_year_start_month)
        return date(year, financial_year_start_month, 1)

    start = date(today.year, today.month, 1) - timedelta(days=months * 30)
    return start.replace(day=1)


async def _asset_value_at(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    primary_currency: str = "USD",
    group_ids: Optional[list[uuid.UUID]] = None,
) -> float:
    """Sum of all active asset values at a given date, converted to primary currency."""
    asset_stmt = select(Asset).where(
        Asset.workspace_id == workspace_id,
        Asset.is_archived == False,
        Asset.sell_date.is_(None),
    )
    if group_ids is not None:
        asset_stmt = asset_stmt.where(Asset.group_id.in_(group_ids))
    asset_result = await session.execute(asset_stmt)
    total = 0.0
    for asset in asset_result.scalars().all():
        val_result = await session.execute(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id))
            .limit(1)
        )
        val = val_result.scalar_one_or_none()
        if val is not None:
            amount = float(val)
        elif asset.purchase_price is not None and (
            asset.purchase_date is None or asset.purchase_date <= cutoff
        ):
            amount = float(asset.purchase_price)
        else:
            amount = 0.0
        if amount > 0:
            converted, _ = await convert(
                session, Decimal(str(amount)), asset.currency, primary_currency, cutoff
            )
            total += float(converted)
    return total


async def _net_worth_at(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    primary_currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    accounts: Optional[list[Account]] = None,
) -> ReportDataPoint:
    """Compute a single net worth snapshot at a given date, converted to primary currency.

    Under a Collection filter (``account_ids`` set), only those accounts are
    summed and only assets in the collection's wallets (``asset_group_ids``)
    are included.

    ``accounts`` lets a caller that already fetched the (date-independent) open
    account list for this workspace/filter pass it in, instead of re-querying
    it for every snapshot in a multi-point trend. Omit it to fetch as before.
    """
    if accounts is None:
        accounts = await _get_open_accounts(session, workspace_id, account_ids)

    accounts_total = 0.0
    liabilities_total = 0.0
    composition: list[ReportCompositionItem] = []

    for account in accounts:
        bal = await _account_balance_at(session, account, cutoff)
        converted, _ = await convert(
            session, Decimal(str(abs(bal))), account.currency, primary_currency, cutoff
        )
        converted_val = float(converted)
        if account.type == "credit_card" or bal < 0:
            liabilities_total += converted_val
            if converted_val > 0:
                composition.append(ReportCompositionItem(
                    key=str(account.id),
                    label=get_account_name(account),
                    value=round(converted_val, 2),
                    color=_ACCOUNT_TYPE_COLORS.get(account.type, "#6B7280"),
                    group="liabilities",
                ))
        else:
            accounts_total += converted_val
            if converted_val > 0:
                composition.append(ReportCompositionItem(
                    key=str(account.id),
                    label=get_account_name(account),
                    value=round(converted_val, 2),
                    color=_ACCOUNT_TYPE_COLORS.get(account.type, "#6B7280"),
                    group="accounts",
                ))

    # Per-asset composition at the cutoff date
    filtered = account_ids is not None
    asset_stmt = select(Asset).where(
        Asset.workspace_id == workspace_id,
        Asset.is_archived == False,
        Asset.sell_date.is_(None),
    )
    if filtered:
        asset_stmt = asset_stmt.where(Asset.group_id.in_(asset_group_ids or []))
    asset_result = await session.execute(asset_stmt)
    assets_total = 0.0
    for asset in asset_result.scalars().all():
        val_result = await session.execute(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id))
            .limit(1)
        )
        val = val_result.scalar_one_or_none()
        if val is not None:
            amount = float(val)
        elif asset.purchase_price is not None and (
            asset.purchase_date is None or asset.purchase_date <= cutoff
        ):
            amount = float(asset.purchase_price)
        else:
            amount = 0.0
        if amount > 0:
            converted, _ = await convert(
                session, Decimal(str(amount)), asset.currency, primary_currency, cutoff
            )
            converted_val = round(float(converted), 2)
            assets_total += converted_val
            composition.append(ReportCompositionItem(
                key=str(asset.id),
                label=asset.name,
                value=converted_val,
                color=_ASSET_TYPE_COLORS.get(asset.type, "#6B7280"),
                group="assets",
            ))

    net_worth = accounts_total + assets_total - liabilities_total

    return ReportDataPoint(
        date=cutoff.isoformat(),
        value=round(net_worth, 2),
        breakdowns={
            "accounts": round(accounts_total, 2),
            "assets": round(assets_total, 2),
            "liabilities": round(liabilities_total, 2),
        },
        composition=composition,
    )


def _format_date_label(d: date, interval: str) -> str:
    """Format a date point based on interval granularity."""
    if interval == "daily":
        return d.isoformat()
    elif interval == "weekly":
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    elif interval == "monthly":
        return f"{d.year}-{d.month:02d}"
    elif interval == "yearly":
        return str(d.year)
    return d.isoformat()


def _date_points(
    start: date, end: date, interval: str
) -> list[date]:
    """Generate date points between start and end for the given interval."""
    points: list[date] = []
    current = start

    if interval == "daily":
        while current <= end:
            points.append(current)
            current += timedelta(days=1)
    elif interval == "weekly":
        while current <= end:
            points.append(current)
            current += timedelta(weeks=1)
    elif interval == "monthly":
        # One snapshot per month: last day of the month, capped at end
        current = date(start.year, start.month, 1)
        while current <= end:
            last_day = calendar.monthrange(current.year, current.month)[1]
            points.append(min(date(current.year, current.month, last_day), end))
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
    elif interval == "yearly":
        while current <= end:
            points.append(current)
            current = date(current.year + 1, current.month, current.day)
    else:
        # Default to monthly
        return _date_points(start, end, "monthly")

    # Ensure the last point uses `end` so the final snapshot reflects today's data.
    # If the last generated point is in the same period as `end`, replace it;
    # otherwise append `end` as a new point.
    if points and points[-1] < end:
        if _format_date_label(points[-1], interval) == _format_date_label(end, interval):
            points[-1] = end  # same label, use today's cutoff
        else:
            points.append(end)

    return points


async def get_net_worth_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 12,
    interval: str = "monthly",
    currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    period: str | None = None,
    financial_year_start_month: int = 1,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ReportResponse:
    """Build a full ReportResponse for net worth over time."""
    # A wallet-only collection (wallets, no accounts) still filters.
    if asset_group_ids is not None and account_ids is None:
        account_ids = []
    real_today = app_today()
    axis_end = end_date or real_today
    start = _report_start_date(
        axis_end, months, period, financial_year_start_month=financial_year_start_month,
        start_date=start_date,
    )

    # Get user's primary currency
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    points = _date_points(start, axis_end, interval)

    # Fetch the open-account list once: it does not depend on the snapshot
    # date, so every point below reuses it instead of re-querying it.
    accounts = await _get_open_accounts(session, workspace_id, account_ids)

    # Compute snapshot at each date point
    trend: list[ReportDataPoint] = []
    for point in points:
        dp = await _net_worth_at(
            session, workspace_id, point, primary_currency, account_ids, asset_group_ids,
            accounts=accounts,
        )
        dp.date = _format_date_label(point, interval)
        dp.change = round(dp.value - trend[-1].value, 2) if trend else None
        trend.append(dp)

    # Current snapshot (last point) for summary; baseline at period start for delta
    current = trend[-1] if trend else ReportDataPoint(
        date="", value=0, breakdowns={"accounts": 0, "assets": 0, "liabilities": 0}
    )
    # The first trend point's *actual* cutoff (`points[0]`, before its `.date`
    # was overwritten with a formatted label) equals `start` for every
    # interval except monthly, which snaps to month-end. Only reuse it then —
    # an approximate match would silently swap in the wrong day's snapshot.
    if trend and points[0] == start:
        baseline = trend[0]
    else:
        baseline = await _net_worth_at(
            session, workspace_id, start, primary_currency, account_ids, asset_group_ids,
            accounts=accounts,
        )
    previous = baseline if trend else current

    change_amount = current.value - previous.value
    change_percent = (
        (change_amount / abs(previous.value) * 100)
        if previous.value != 0
        else None
    )

    summary = ReportSummary(
        primary_value=current.value,
        change_amount=round(change_amount, 2),
        change_percent=round(change_percent, 2) if change_percent is not None else None,
        breakdowns=[
            ReportBreakdown(
                key="accounts",
                label="Accounts",
                value=current.breakdowns.get("accounts", 0),
                color="#6366F1",
            ),
            ReportBreakdown(
                key="assets",
                label="Assets",
                value=current.breakdowns.get("assets", 0),
                color="#F59E0B",
            ),
            ReportBreakdown(
                key="liabilities",
                label="Liabilities",
                value=current.breakdowns.get("liabilities", 0),
                color="#F43F5E",
            ),
        ],
    )

    meta = ReportMeta(
        type="net_worth",
        series_keys=["accounts", "assets", "liabilities"],
        currency=primary_currency,
        interval=interval,
    )

    # Reuse the selected end date's per-item composition for the response-level
    # `composition` field (final snapshot for the donut).
    composition = trend[-1].composition if trend else []

    return ReportResponse(summary=summary, trend=trend, meta=meta, composition=composition)


def _add_months(d: date, months: int) -> date:
    """Advance a date by N months, clamping the day to the target month's length."""
    new_month = d.month + months
    new_year = d.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1
    last_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(d.day, last_day)
    return date(new_year, new_month, new_day)


_BASELINE_MAX_LOOKBACK_MONTHS = 12
_NOT_AN_INSTALLMENT = (
    Transaction.total_installments.is_(None),
    Transaction.installment_series_id.is_(None),
)
_PAST_HISTORY_MONTHS = 1


async def _get_baseline_projection(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    today: date,
    end: date,
    primary_currency: str,
    to_primary,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> tuple[list[dict], int]:
    """Estimate future flows by averaging the user's recent transaction history.

    Replaces deterministic recurring projections when baseline mode is on:
    sums all non-ignored, P&L-counting transactions inside the look-back
    window, splits into mean daily inflow and outflow, then emits one
    synthetic flow per day from today+1 to end.

    The look-back window is **adaptive**: capped at
    ``_BASELINE_MAX_LOOKBACK_MONTHS`` (12) but shrunk to the user's earliest
    qualifying transaction date when they have less history. This means a
    user with 4 months of activity gets a 4-month average; a user with
    3 years gets a 12-month average. More history → more stable estimate;
    less history → still works, just noisier.

    Installment rows (``total_installments`` or ``installment_series_id`` set)
    stay out of the average: the future ones are already booked and added to
    the walk once, so counting the past ones in the mean too would charge each
    instalment twice.

    Symmetric for income and expense — fixes the "no recurring salary set
    up, chart looks catastrophic" case as well as the "no recurring expenses
    set up, chart looks rosy" case.

    Returns ``(projections, lookback_days)`` so the caller can surface the
    actual window used in the response (zero when there's no history).
    """
    acct_filter = [Transaction.account_id.in_(account_ids)] if account_ids is not None else []
    cap_start = _add_months(today, -_BASELINE_MAX_LOOKBACK_MONTHS)
    earliest_result = await session.execute(
        select(func.min(Transaction.date))
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.date <= today,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_pnl(),
            *_NOT_AN_INSTALLMENT,
            *acct_filter,
        )
    )
    earliest_date = earliest_result.scalar_one_or_none()
    if earliest_date is None:
        return [], 0
    window_start = max(earliest_date, cap_start)

    rows = await session.execute(
        select(
            Transaction.type,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.date >= window_start,
            Transaction.date <= today,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_pnl(),
            *_NOT_AN_INSTALLMENT,
            *acct_filter,
        )
    )
    total_inflow_primary = 0.0
    total_outflow_primary = 0.0
    for tx_type, amt, amt_primary, ccy in rows.all():
        if amt_primary is not None:
            amount = float(amt_primary)
        else:
            amount = await to_primary(Decimal(str(amt or 0)), ccy)
        if amount == 0:
            continue
        if tx_type == "credit":
            total_inflow_primary += abs(amount)
        else:
            total_outflow_primary += abs(amount)

    lookback_days = max((today - window_start).days, 1)
    daily_inflow = total_inflow_primary / lookback_days
    daily_outflow = total_outflow_primary / lookback_days

    projections: list[dict] = []
    if daily_inflow == 0 and daily_outflow == 0:
        return projections, lookback_days

    cursor = today + timedelta(days=1)
    while cursor <= end:
        if daily_inflow > 0:
            projections.append({
                "date": cursor,
                "amount": daily_inflow,
                "currency": primary_currency,
                "type": "credit",
                "category_id": None,
            })
        if daily_outflow > 0:
            projections.append({
                "date": cursor,
                "amount": daily_outflow,
                "currency": primary_currency,
                "type": "debit",
                "category_id": None,
            })
        cursor = cursor + timedelta(days=1)
    return projections, lookback_days


async def get_cash_flow_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 6,
    interval: str = "daily",
    currency: str = "USD",
    baseline: bool = False,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> ReportResponse:
    """Cash flow chart with a short past window plus a forward projection.

    Past window (``_PAST_HISTORY_MONTHS`` months back to today) shows actual
    booked transactions so the user has a visible "this is real" anchor next
    to the forecast. Forward window walks from today to ``today + months``
    applying either deterministic recurring projections (default) or a
    historical-mean baseline (when ``baseline=True``) — see
    ``_get_baseline_projection`` for the latter.

    Respects the global ``credit_card_accounting_mode`` setting:
      - **cash**: flows queried by ``Transaction.date``.
      - **accrual**: flows queried by ``Transaction.effective_date`` so CC
        purchases show up as cash leaving on their bill due date. The
        balance at past-history start is also adjusted to add back any
        pending CC purchases whose effective_date is in the future window,
        avoiding double-counting against ``_balance_at``.
    """
    from app.services.dashboard_service import _balance_at, _get_recurring_projections
    from app.services.fx_rate_service import get_rate

    acct_filter = [Transaction.account_id.in_(account_ids)] if account_ids is not None else []
    today = app_today()
    end = _add_months(today, months)
    chart_start = _add_months(today, -_PAST_HISTORY_MONTHS)

    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    accounting_mode = await get_credit_card_accounting_mode(session)
    accrual = accounting_mode == "accrual"

    # "Saldo Atual" shown in the hero card. The walk is anchored at this
    # value (not at balance-at-chart_start) so opening-balance transactions
    # inside the past-history window can't introduce drift.
    current_balance = await _balance_at(
        session, workspace_id, today, primary_currency_hint=primary_currency,
        account_ids=account_ids,
    )

    rate_cache: dict[str, Decimal] = {primary_currency: Decimal("1")}

    async def _to_primary(amount: Decimal, ccy: str) -> float:
        if ccy == primary_currency:
            return float(amount)
        rate = rate_cache.get(ccy)
        if rate is None:
            rate = await get_rate(session, ccy, primary_currency)
            rate_cache[ccy] = rate
        return float((amount * rate).quantize(Decimal("0.01")))

    flows: dict[date, dict[str, float]] = {}

    def _add_flow(d: date, amount: float, is_credit: bool) -> None:
        bucket = flows.setdefault(d, {"inflow": 0.0, "outflow": 0.0})
        if is_credit:
            bucket["inflow"] += amount
        else:
            bucket["outflow"] += amount

    flow_date_col = Transaction.effective_date if accrual else Transaction.date

    # 1a. Past actual transactions (chart_start, today]. Gives the chart a
    #     "real" section before the forecast so the today-marker has meaning.
    past_result = await session.execute(
        select(
            flow_date_col,
            Transaction.type,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            flow_date_col > chart_start,
            flow_date_col <= today,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_pnl(),
            *acct_filter,
        )
    )
    for flow_date, tx_type, amt, amt_primary, ccy in past_result.all():
        if amt_primary is not None:
            amount_primary = float(amt_primary)
        else:
            amount_primary = await _to_primary(Decimal(str(amt or 0)), ccy)
        if amount_primary == 0:
            continue
        _add_flow(flow_date, abs(amount_primary), tx_type == "credit")

    # 1b. Future booked transactions whose cash impact is past today.
    booked_result = await session.execute(
        select(
            flow_date_col,
            Transaction.type,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            flow_date_col > today,
            flow_date_col <= end,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_pnl(),
            *acct_filter,
        )
    )
    for row in booked_result.all():
        flow_date, tx_type, amt, amt_primary, ccy = row
        if amt_primary is not None:
            amount_primary = float(amt_primary)
        else:
            amount_primary = await _to_primary(Decimal(str(amt or 0)), ccy)
        if amount_primary == 0:
            continue
        _add_flow(flow_date, abs(amount_primary), tx_type == "credit")

    # 2. Pending rows are forecast rows. They never enter the past/actual
    # section, including pending credit-card purchases in accrual mode.
    # Use the effective bill date when cash leaves on the card's due date.
    # Pending rows whose forecast date is already past still belong to the
    # payable position today, so carry them into the forward projected walk
    # instead of silently dropping them.
    pending_current_delta = 0.0
    pending_forecast = await _get_forecast_transactions(
        session, workspace_id, date.min, end + timedelta(days=1), account_ids,
        range_date_col=flow_date_col,
    )
    for tx in pending_forecast:
        if tx.status != "pending" or not _counts_as_user_pnl_row(tx):
            continue
        flow_date = tx.effective_bill_date or (tx.effective_date if accrual else tx.date)
        if flow_date > end:
            continue
        if tx.amount_primary is not None:
            amount_primary = abs(float(tx.amount_primary))
        else:
            amount_primary = await _to_primary(Decimal(str(abs(tx.amount))), tx.currency)
        if amount_primary:
            if flow_date <= today:
                # A connected account's provider number is authoritative and
                # may already include this pending row. Keep provider parity
                # rather than guessing whether it was included.
                if not (tx.account and tx.account.connection_id):
                    pending_current_delta += amount_primary if tx.type == "credit" else -amount_primary
            else:
                _add_flow(flow_date, amount_primary, tx.type == "credit")

    # In accrual mode, a posted card purchase made today is already part of a
    # manual card's liability, while its cash impact belongs on the future bill
    # date. Move that posted amount back into the projected starting position
    # before the booked future flow is applied. Pending rows intentionally do
    # not use this adjustment: their posted-only current balance has excluded
    # them and the generic pending handling above is sufficient.
    if accrual:
        for tx in pending_forecast:
            if tx.status != "posted" or not _counts_as_user_pnl_row(tx):
                continue
            if not tx.account or tx.account.type != "credit_card" or tx.date > today:
                continue
            flow_date = tx.effective_bill_date or tx.effective_date
            if flow_date <= today or flow_date > end:
                continue
            if tx.amount_primary is not None:
                amount_primary = abs(float(tx.amount_primary))
            else:
                amount_primary = await _to_primary(Decimal(str(abs(tx.amount))), tx.currency)
            if tx.type == "debit":
                current_balance += amount_primary
            else:
                current_balance -= amount_primary

    # 3. Forward projection. Default: deterministic recurring rules.
    #    Baseline mode: replace them with a historical-mean estimate so the
    #    chart reflects the user's actual recent lifestyle, not just what
    #    they've explicitly marked as recurring.
    cat_totals: dict[tuple[str, str], dict] = {}
    cat_cache: dict[str, dict] = {}
    baseline_lookback_days = 0

    if baseline:
        projections, baseline_lookback_days = await _get_baseline_projection(
            session, workspace_id, today, end, primary_currency, _to_primary, account_ids,
        )
    else:
        projections = await _get_recurring_projections(
            session, workspace_id, today + timedelta(days=1), end + timedelta(days=1),
            account_ids,
        )

    for proj in projections:
        d = proj["date"]
        if d <= today or d > end:
            continue
        amount_primary = await _to_primary(
            Decimal(str(proj["amount"])), proj["currency"]
        )
        if amount_primary == 0:
            continue
        _add_flow(d, amount_primary, proj["type"] == "credit")

        cat_id = proj["category_id"]
        if cat_id:
            cat_id_str = str(cat_id)
        else:
            cat_id_str = "baseline" if baseline else "uncategorized"
        group = "income" if proj["type"] == "credit" else "expenses"
        if cat_id and cat_id_str not in cat_cache:
            cat_row = await session.execute(
                select(Category.name, Category.color).where(Category.id == cat_id)
            )
            row = cat_row.one_or_none()
            cat_cache[cat_id_str] = {
                "label": row[0] if row else "Uncategorized",
                "color": row[1] if row else "#6B7280",
            }
        elif cat_id_str == "baseline" and cat_id_str not in cat_cache:
            # Frontend translates via reports.baseline; label is the fallback.
            cat_cache[cat_id_str] = {"label": "Baseline", "color": "#94A3B8"}
        info = cat_cache.get(cat_id_str, {"label": "Uncategorized", "color": "#6B7280"})
        key = (cat_id_str, group)
        if key not in cat_totals:
            cat_totals[key] = {"label": info["label"], "color": info["color"], "value": 0.0}
        cat_totals[key]["value"] += amount_primary

    # 3b. Invoices still owed, on the day they were promised for. An
    #     invoice is a claim rather than a movement, which is why it never
    #     reached this chart before: until matching could say that an open
    #     invoice and a pending bank credit were the same money, adding
    #     both would have inflated every projection in the product.
    #
    #     Only the unallocated balance is carried, so a claim and the
    #     payment that settles it never both appear. Skipped when the
    #     chart is narrowed to particular accounts: a claim has no account
    #     until somebody pays it.
    # `is None`, not falsy: a collection holding only wallets narrows
    # the report to an empty set of bank accounts, and an empty list
    # means filtered to nothing rather than not filtered at all.
    if account_ids is None:
        for claim in await invoice_forecast_service.claims_in_range(
            session, workspace_id, today + timedelta(days=1), end + timedelta(days=1)
        ):
            claim_primary = await _to_primary(claim.amount, claim.currency)
            if claim_primary == 0:
                continue
            _add_flow(claim.due_date, abs(claim_primary), claim.direction == "receivable")

    # 4. Walk day-by-day. The actual section is anchored at today's
    # authoritative balance. The forward projected section starts from that
    # balance plus pending payables already dated today or earlier.
    #    balance (``current_balance`` from ``_balance_at``) rather than at
    #    ``chart_starting_balance``: opening-balance transactions are excluded
    #    from the past-actuals query, so a forward walk seeded from
    #    ``chart_starting_balance`` would drift if any opening balance falls
    #    inside the past-history window. Anchoring at today eliminates that
    #    class of bug and keeps the past trend visually correct.
    daily_balance: dict[date, float] = {today: current_balance}
    daily_inflow: dict[date, float] = {}
    daily_outflow: dict[date, float] = {}

    # Today's own inflow/outflow bucket (for tooltip), but the balance at
    # end-of-today is already current_balance regardless of today's flows
    # (they're folded into the authoritative number from _balance_at).
    today_bucket = flows.get(today, {"inflow": 0.0, "outflow": 0.0})
    daily_inflow[today] = today_bucket["inflow"]
    daily_outflow[today] = today_bucket["outflow"]

    # Backward walk: balance(d-1) = balance(d) - net_flow(d).
    running = current_balance
    cursor_d = today
    while cursor_d > chart_start:
        bucket = flows.get(cursor_d, {"inflow": 0.0, "outflow": 0.0})
        running -= bucket["inflow"] - bucket["outflow"]
        cursor_d = cursor_d - timedelta(days=1)
        daily_balance[cursor_d] = running
        prev_bucket = flows.get(cursor_d, {"inflow": 0.0, "outflow": 0.0})
        daily_inflow[cursor_d] = prev_bucket["inflow"]
        daily_outflow[cursor_d] = prev_bucket["outflow"]

    # Forward walk: balance(d+1) = balance(d) + net_flow(d+1). Pending rows
    # already due are part of the projected starting position, not a second
    # dated flow in the future.
    #
    # The walk also tracks the runway: the first day the projected balance
    # drops below zero, and the lowest point of the horizon. Both are taken
    # from the daily walk so a dip inside a week or month still counts when
    # the chart is aggregated to a coarser interval.
    running = current_balance + pending_current_delta
    lowest_balance, lowest_balance_date = running, today
    runway_date: date | None = today if running < 0 else None
    cursor_d = today
    while cursor_d < end:
        cursor_d = cursor_d + timedelta(days=1)
        bucket = flows.get(cursor_d, {"inflow": 0.0, "outflow": 0.0})
        running += bucket["inflow"] - bucket["outflow"]
        daily_balance[cursor_d] = running
        daily_inflow[cursor_d] = bucket["inflow"]
        daily_outflow[cursor_d] = bucket["outflow"]
        if running < lowest_balance:
            lowest_balance, lowest_balance_date = running, cursor_d
        if runway_date is None and running < 0:
            runway_date = cursor_d

    # 5. Aggregate to interval.
    points = _date_points(chart_start, end, interval)
    trend: list[ReportDataPoint] = []

    if interval == "daily":
        for p in points:
            bal = daily_balance.get(p, running)
            inf = daily_inflow.get(p, 0.0)
            outf = daily_outflow.get(p, 0.0)
            trend.append(ReportDataPoint(
                date=_format_date_label(p, "daily"),
                value=round(bal, 2),
                breakdowns={"inflow": round(inf, 2), "outflow": round(outf, 2)},
            ))
    else:
        groups: dict[str, dict] = {}
        cur = chart_start
        while cur <= end:
            label = _format_date_label(cur, interval)
            g = groups.setdefault(
                label,
                {"balance": daily_balance.get(cur, running), "last_d": cur,
                 "inflow": 0.0, "outflow": 0.0},
            )
            if cur >= g["last_d"]:
                g["balance"] = daily_balance.get(cur, running)
                g["last_d"] = cur
            g["inflow"] += daily_inflow.get(cur, 0.0)
            g["outflow"] += daily_outflow.get(cur, 0.0)
            cur = cur + timedelta(days=1)

        seen: set[str] = set()
        for p in points:
            label = _format_date_label(p, interval)
            if label in seen:
                continue
            seen.add(label)
            g = groups.get(label)
            if g is None:
                continue
            trend.append(ReportDataPoint(
                date=label,
                value=round(g["balance"], 2),
                breakdowns={
                    "inflow": round(g["inflow"], 2),
                    "outflow": round(g["outflow"], 2),
                },
            ))

    # 6. Summary. "Projected income/expenses" sums only the forward portion,
    #    not the past actuals that the chart now also includes.
    ending_balance = trend[-1].value if trend else round(current_balance, 2)
    forward_inflow = 0.0
    forward_outflow = 0.0
    for d, bucket in flows.items():
        if today < d <= end:
            forward_inflow += bucket["inflow"]
            forward_outflow += bucket["outflow"]
    total_inflow = round(forward_inflow, 2)
    total_outflow = round(forward_outflow, 2)
    change_amount = round(ending_balance - current_balance, 2)
    change_percent = (
        (change_amount / abs(current_balance) * 100) if current_balance != 0 else None
    )

    summary = ReportSummary(
        primary_value=ending_balance,
        change_amount=change_amount,
        change_percent=round(change_percent, 2) if change_percent is not None else None,
        breakdowns=[
            ReportBreakdown(
                key="startingBalance", label="Starting Balance",
                value=round(current_balance, 2), color="#6366F1",
            ),
            ReportBreakdown(
                key="projectedIncome", label="Projected Income",
                value=total_inflow, color="#10B981",
            ),
            ReportBreakdown(
                key="projectedExpenses", label="Projected Expenses",
                value=total_outflow, color="#F43F5E",
            ),
            ReportBreakdown(
                key="endingBalance", label="Ending Balance",
                value=ending_balance, color="#8B5CF6",
            ),
        ],
    )

    meta = ReportMeta(
        type="cash_flow",
        series_keys=["balance"],
        currency=primary_currency,
        interval=interval,
        forecast_start_date=_format_date_label(today, interval),
        baseline_active=baseline,
        baseline_lookback_days=baseline_lookback_days if baseline else None,
        runway_date=runway_date.isoformat() if runway_date else None,
        lowest_balance=round(lowest_balance, 2),
        lowest_balance_date=lowest_balance_date.isoformat(),
    )

    composition: list[ReportCompositionItem] = []
    for (cat_key, group), info in cat_totals.items():
        if info["value"] <= 0:
            continue
        composition.append(ReportCompositionItem(
            key=cat_key,
            label=info["label"],
            value=round(info["value"], 2),
            color=info["color"],
            group=group,
        ))

    return ReportResponse(
        summary=summary, trend=trend, meta=meta,
        composition=composition, category_trend=[],
    )
