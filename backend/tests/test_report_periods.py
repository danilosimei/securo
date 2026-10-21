"""Presets of the report period list, with fixed dates."""
from datetime import date
from typing import cast

import pytest
from fastapi import HTTPException

from app.services.reports.periods import resolve_period

TODAY = date(2026, 10, 21)


def _dates(p):
    return (p.from_, p.to, p.previous_from, p.previous_to)


def test_this_month_is_partial_and_compares_the_same_days_of_last_month():
    p = resolve_period("this_month", TODAY)
    assert _dates(p) == (date(2026, 10, 1), TODAY, date(2026, 9, 1), date(2026, 9, 21))
    assert p.partial and p.months == 1


def test_this_month_clamps_the_previous_day_in_a_shorter_month():
    p = resolve_period("this_month", date(2026, 3, 31))
    assert (p.previous_from, p.previous_to) == (date(2026, 2, 1), date(2026, 2, 28))


def test_last_month_is_the_whole_previous_month():
    p = resolve_period("last_month", TODAY)
    assert _dates(p) == (date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 1), date(2026, 8, 31))
    assert not p.partial


def test_last_month_in_january_crosses_the_year_and_february_is_whole():
    p = resolve_period("last_month", date(2026, 1, 10))
    assert (p.from_, p.to) == (date(2025, 12, 1), date(2025, 12, 31))
    assert (p.previous_from, p.previous_to) == (date(2025, 11, 1), date(2025, 11, 30))
    p = resolve_period("last_month", date(2024, 3, 5))
    assert (p.from_, p.to) == (date(2024, 2, 1), date(2024, 2, 29))
    assert (p.previous_from, p.previous_to) == (date(2024, 1, 1), date(2024, 1, 31))


def test_last_12m_is_twelve_closed_months():
    p = resolve_period("last_12m", TODAY)
    assert _dates(p) == (date(2025, 10, 1), date(2026, 9, 30), date(2024, 10, 1), date(2025, 9, 30))
    assert not p.partial and p.months == 12


def test_last_3m_and_6m():
    p = resolve_period("last_3m", TODAY)
    assert _dates(p) == (date(2026, 7, 1), date(2026, 9, 30), date(2026, 4, 1), date(2026, 6, 30))
    p = resolve_period("last_6m", date(2026, 2, 15))
    assert _dates(p) == (date(2025, 8, 1), date(2026, 1, 31), date(2025, 2, 1), date(2025, 7, 31))


def test_last_n_months_in_january_end_in_december():
    p = resolve_period("last_3m", date(2026, 1, 1))
    assert (p.from_, p.to) == (date(2025, 10, 1), date(2025, 12, 31))


def test_ytd_runs_from_january_to_today():
    p = resolve_period("ytd", TODAY)
    assert _dates(p) == (date(2026, 1, 1), TODAY, date(2025, 1, 1), date(2025, 10, 21))
    assert p.partial and p.months == 10


def test_ytd_uses_the_fiscal_year_start():
    assert resolve_period("ytd", date(2026, 2, 10), fy_start_month=4).from_ == date(2025, 4, 1)
    assert resolve_period("ytd", date(2026, 4, 1), fy_start_month=4).from_ == date(2026, 4, 1)


def test_ytd_on_feb_29_compares_to_feb_28():
    p = resolve_period("ytd", date(2024, 2, 29))
    assert p.previous_to == date(2023, 2, 28)


def test_custom_compares_the_same_number_of_days_before():
    p = resolve_period("custom", TODAY, from_=date(2026, 3, 1), to=date(2026, 3, 31))
    assert _dates(p) == (date(2026, 3, 1), date(2026, 3, 31), date(2026, 1, 29), date(2026, 2, 28))
    assert not p.partial and p.months == 1


@pytest.mark.parametrize(
    ("from_", "to", "code"),
    [
        (None, None, "period_custom_dates"),
        (date(2026, 1, 1), None, "period_custom_dates"),
        (date(2026, 3, 2), date(2026, 3, 1), "period_custom_dates"),
        (date(2026, 1, 1), date(2026, 10, 22), "period_custom_range"),
        (date(2015, 1, 1), date(2026, 1, 1), "period_custom_range"),
    ],
)
def test_custom_rejects_bad_dates(from_, to, code):
    with pytest.raises(HTTPException) as exc:
        resolve_period("custom", TODAY, from_=from_, to=to)
    assert exc.value.status_code == 422
    assert cast(dict, exc.value.detail)["code"] == code
    assert cast(dict, exc.value.detail)["message"]


def test_unknown_period_is_rejected():
    with pytest.raises(HTTPException) as exc:
        resolve_period("last_year", TODAY)
    assert cast(dict, exc.value.detail)["code"] == "period_invalid"
