"""tests/test_detection.py"""

from datetime import date, timedelta

from custom_components.aquawatch.detection import (
    detect_statistical_anomaly,
    detect_sustained_leak,
)
from custom_components.aquawatch.models import ConsumptionRecord


def _records(liters_by_day: list[float], start: date = date(2024, 1, 1)):
    return [
        ConsumptionRecord(
            record_date=start + timedelta(days=i),
            liters=liters,
            cumulative_index_m3=0.0,
            is_estimated=False,
        )
        for i, liters in enumerate(liters_by_day)
    ]


def test_sustained_leak_detected_when_recent_days_exceed_threshold() -> None:
    records = _records([200.0] * 5 + [350.0, 350.0])
    assert (
        detect_sustained_leak(
            records, baseline_days=5, threshold_ratio=1.5, consecutive_days_required=2
        )
        is True
    )


def test_sustained_leak_not_detected_when_one_day_is_normal() -> None:
    records = _records([200.0] * 5 + [250.0, 350.0])
    assert (
        detect_sustained_leak(
            records, baseline_days=5, threshold_ratio=1.5, consecutive_days_required=2
        )
        is False
    )


def test_sustained_leak_not_detected_with_insufficient_history() -> None:
    records = _records([350.0, 350.0])
    assert (
        detect_sustained_leak(
            records, baseline_days=5, threshold_ratio=1.5, consecutive_days_required=2
        )
        is False
    )


def test_statistical_anomaly_detected_on_large_spike() -> None:
    records = _records([200.0, 205.0, 195.0, 210.0, 190.0, 400.0])
    assert (
        detect_statistical_anomaly(records, baseline_days=5, zscore_threshold=2.5)
        is True
    )


def test_statistical_anomaly_not_detected_on_normal_day() -> None:
    records = _records([200.0, 205.0, 195.0, 210.0, 190.0, 205.0])
    assert (
        detect_statistical_anomaly(records, baseline_days=5, zscore_threshold=2.5)
        is False
    )


def test_statistical_anomaly_not_detected_with_insufficient_history() -> None:
    records = _records([400.0])
    assert (
        detect_statistical_anomaly(records, baseline_days=5, zscore_threshold=2.5)
        is False
    )
