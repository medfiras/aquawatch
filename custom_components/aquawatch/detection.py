"""Leak and anomaly detection heuristics for AquaWatch.

The SEDIF API only exposes daily totals, so leak detection is defined as
sustained above-baseline consumption over consecutive days rather than a
night-time flow window.
"""

from __future__ import annotations

from statistics import mean, pstdev

from .models import ConsumptionRecord


def detect_sustained_leak(
    records: list[ConsumptionRecord],
    baseline_days: int,
    threshold_ratio: float,
    consecutive_days_required: int,
) -> bool:
    """Return True if the most recent days show sustained above-baseline usage."""
    if len(records) < baseline_days + consecutive_days_required:
        return False

    ordered = sorted(records, key=lambda r: r.record_date)
    recent = ordered[-consecutive_days_required:]
    baseline_window = ordered[
        -(baseline_days + consecutive_days_required) : -consecutive_days_required
    ]

    baseline = mean(r.liters for r in baseline_window)
    if baseline <= 0:
        return False

    threshold = baseline * threshold_ratio
    return all(r.liters > threshold for r in recent)


def detect_statistical_anomaly(
    records: list[ConsumptionRecord],
    baseline_days: int,
    zscore_threshold: float,
) -> bool:
    """Return True if the latest day's consumption is a statistical outlier."""
    if len(records) < baseline_days + 1:
        return False

    ordered = sorted(records, key=lambda r: r.record_date)
    latest = ordered[-1]
    baseline_window = ordered[-(baseline_days + 1) : -1]

    baseline_mean = mean(r.liters for r in baseline_window)
    baseline_stdev = pstdev(r.liters for r in baseline_window)
    if baseline_stdev == 0:
        return False

    zscore = (latest.liters - baseline_mean) / baseline_stdev
    return zscore >= zscore_threshold
