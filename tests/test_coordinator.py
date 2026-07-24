"""tests/test_coordinator.py"""

from datetime import date, datetime, timedelta

import pytest

from custom_components.aquawatch.coordinator import _percent_change
from custom_components.aquawatch.models import ConsumptionRecord


def _record(day: date, liters: float) -> ConsumptionRecord:
    return ConsumptionRecord(
        record_date=day, liters=liters, cumulative_index_m3=0.0, is_estimated=False
    )


def test_percent_change_computes_week_over_week_increase() -> None:
    today = date(2024, 3, 15)
    records = []
    # previous week: 7 days at 100L
    for i in range(7, 14):
        records.append(_record(today - timedelta(days=i), 100.0))
    # current week (last 7 days incl. today): 7 days at 150L
    for i in range(0, 7):
        records.append(_record(today - timedelta(days=i), 150.0))

    result = _percent_change(records, today, days_back=7)

    assert result == pytest.approx(50.0)


def test_percent_change_returns_none_without_previous_period() -> None:
    today = date(2024, 3, 15)
    records = [_record(today - timedelta(days=i), 150.0) for i in range(0, 7)]

    assert _percent_change(records, today, days_back=7) is None


def test_percent_change_returns_none_with_empty_records() -> None:
    assert _percent_change([], date(2024, 3, 15), days_back=7) is None
