"""tests/test_forecast.py"""

from datetime import date, timedelta

import pytest

from custom_components.aquawatch.forecast import (
    forecast_month_end_cost,
    forecast_month_end_volume_m3,
)
from custom_components.aquawatch.models import ConsumptionRecord


def _record(day: int, liters: float) -> ConsumptionRecord:
    return ConsumptionRecord(
        record_date=date(2024, 3, day),
        liters=liters,
        cumulative_index_m3=0.0,
        is_estimated=False,
    )


def test_forecast_volume_weights_recent_days() -> None:
    records = [_record(d, 200.0) for d in range(1, 8)] + [
        _record(8, 300.0),
        _record(9, 300.0),
        _record(10, 300.0),
    ]
    today = date(2024, 3, 10)

    result = forecast_month_end_volume_m3(records, today)

    # plain_avg = (7*200 + 3*300) / 10 = 230; recent_avg (last 3) = 300
    # weighted = 0.6*230 + 0.4*300 = 258; forecast_liters = 258*31 = 7998
    assert result == pytest.approx(7.998)


def test_forecast_volume_returns_none_without_data_this_month() -> None:
    records = [_record(15, 200.0)]
    today = date(2024, 4, 1)
    assert forecast_month_end_volume_m3(records, today) is None


def test_forecast_cost_multiplies_volume_by_price() -> None:
    records = [_record(d, 200.0) for d in range(1, 8)] + [
        _record(8, 300.0),
        _record(9, 300.0),
        _record(10, 300.0),
    ]
    today = date(2024, 3, 10)

    result = forecast_month_end_cost(records, today, price_per_m3=4.0)

    assert result == pytest.approx(7.998 * 4.0)


def test_forecast_cost_returns_none_without_data() -> None:
    assert forecast_month_end_cost([], date(2024, 3, 10), price_per_m3=4.0) is None
