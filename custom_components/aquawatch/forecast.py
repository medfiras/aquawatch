"""End-of-month consumption and cost forecasting for AquaWatch."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from .models import ConsumptionRecord

_RECENT_DAYS_WEIGHT = 0.4
_PLAIN_AVERAGE_WEIGHT = 0.6
_RECENT_DAYS_COUNT = 3


def forecast_month_end_volume_m3(
    records: list[ConsumptionRecord], today: date
) -> float | None:
    """Project the total m3 consumption for the current month.

    Blends the plain daily average so far this month with the average of the
    last 3 available days (weighted 60/40) to reduce noise from a single
    unusually high or low day.
    """
    month_records = [
        r
        for r in records
        if r.record_date.month == today.month and r.record_date.year == today.year
    ]
    if not month_records:
        return None

    days_elapsed = today.day
    total_liters_so_far = sum(r.liters for r in month_records)
    plain_avg = total_liters_so_far / days_elapsed

    last_three = sorted(month_records, key=lambda r: r.record_date)[
        -_RECENT_DAYS_COUNT:
    ]
    recent_avg = sum(r.liters for r in last_three) / len(last_three)

    weighted_avg = _PLAIN_AVERAGE_WEIGHT * plain_avg + _RECENT_DAYS_WEIGHT * recent_avg

    _, days_in_month = monthrange(today.year, today.month)
    forecast_liters = weighted_avg * days_in_month
    return forecast_liters / 1000


def forecast_month_end_cost(
    records: list[ConsumptionRecord], today: date, price_per_m3: float
) -> float | None:
    """Project the total cost in euros for the current month."""
    volume = forecast_month_end_volume_m3(records, today)
    if volume is None:
        return None
    return volume * price_per_m3
