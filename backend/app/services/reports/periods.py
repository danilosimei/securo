"""Report periods: the closed list of presets and the dates each one means.

Every new report tab shares this, so "last 12 months" is the same twelve
closed months everywhere, and the comparison period is always the stretch of
the same size right before it.
"""
import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta

from fastapi import HTTPException

PERIODS = ("this_month", "last_month", "last_3m", "last_6m", "last_12m", "ytd", "custom")

# Same cap as the older reports endpoints: wide windows tie up the database.
MAX_CUSTOM_RANGE_YEARS = 10

_LAST_N_MONTHS = {"last_3m": 3, "last_6m": 6, "last_12m": 12}


@dataclass(frozen=True)
class Period:
    from_: date
    to: date
    partial: bool
    previous_from: date
    previous_to: date
    #: Calendar months the period touches (first and last may be partial).
    months: int


def _error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _shift_months(value: date, months: int) -> date:
    """`value` moved by whole months, clamping the day to the target month."""
    index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _count_months(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # Feb 29 into a common year
        return date(value.year + years, 2, 28)


def resolve_period(
    period: str,
    today: date,
    from_: date | None = None,
    to: date | None = None,
    fy_start_month: int = 1,
) -> Period:
    if period == "this_month":
        start, end, partial = _month_start(today), today, True
        previous_from = _shift_months(start, -1)
        previous_to = _shift_months(today, -1)
    elif period == "last_month":
        start = _shift_months(_month_start(today), -1)
        end, partial = _month_end(start), False
        previous_from = _shift_months(start, -1)
        previous_to = _month_end(previous_from)
    elif period in _LAST_N_MONTHS:
        count = _LAST_N_MONTHS[period]
        start = _shift_months(_month_start(today), -count)
        end, partial = _month_start(today) - timedelta(days=1), False
        previous_from = _shift_months(start, -count)
        previous_to = start - timedelta(days=1)
    elif period == "ytd":
        year = today.year if today.month >= fy_start_month else today.year - 1
        start, end, partial = date(year, fy_start_month, 1), today, True
        previous_from = _shift_months(start, -12)
        previous_to = _shift_months(today, -12)
    elif period == "custom":
        start, end = _validate_custom(from_, to, today)
        partial = False
        days = (end - start).days + 1
        previous_to = start - timedelta(days=1)
        previous_from = previous_to - timedelta(days=days - 1)
    else:
        raise _error("period_invalid", f"Unknown period: {period}")
    return Period(
        from_=start,
        to=end,
        partial=partial,
        previous_from=previous_from,
        previous_to=previous_to,
        months=_count_months(start, end),
    )


def _validate_custom(from_: date | None, to: date | None, today: date) -> tuple[date, date]:
    if from_ is None or to is None:
        raise _error("period_custom_dates", "A custom period needs both from and to dates")
    if from_ > to:
        raise _error("period_custom_dates", "The start date must be on or before the end date")
    if to > today:
        raise _error("period_custom_range", "The end date must be on or before today")
    if to > _add_years(from_, MAX_CUSTOM_RANGE_YEARS):
        raise _error(
            "period_custom_range",
            f"The custom period is too wide (max {MAX_CUSTOM_RANGE_YEARS} years)",
        )
    return from_, to


#: What a period can be compared with: the stretch right before it (the
#: default), the same dates a year earlier, or any range of choice.
COMPARE_MODES = ("previous", "year_ago", "custom")


def compare_period(
    period: Period,
    mode: str,
    from_: date | None = None,
    to: date | None = None,
) -> Period:
    """`period` with its comparison range swapped for the one `mode` names."""
    if mode == "previous":
        return period
    if mode == "year_ago":
        previous_from, previous_to = _shift_months(period.from_, -12), _shift_months(period.to, -12)
    elif mode == "custom":
        if from_ is None or to is None or from_ > to:
            raise _error("compare_dates", "A custom comparison needs a start and an end, in order")
        if to > _add_years(from_, MAX_CUSTOM_RANGE_YEARS):
            raise _error(
                "compare_dates",
                f"The comparison period is too wide (max {MAX_CUSTOM_RANGE_YEARS} years)",
            )
        previous_from, previous_to = from_, to
    else:
        raise _error("compare_invalid", f"Unknown comparison: {mode}")
    return replace(period, previous_from=previous_from, previous_to=previous_to)
