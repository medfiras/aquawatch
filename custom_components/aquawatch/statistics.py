"""Long-term statistics helpers for AquaWatch (Energy dashboard integration)."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import ConsumptionRecord


def statistic_id_for_entry(entry_id: str) -> str:
    """Return the external statistic_id used for a given config entry."""
    return f"{DOMAIN}:{entry_id}_consumption"


def async_push_records(
    hass: HomeAssistant,
    statistic_id: str,
    entry_title: str,
    records: list[ConsumptionRecord],
    running_sum_start: float,
) -> float:
    """Push records into long-term statistics, returning the updated running sum."""
    if not records:
        return running_sum_start

    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        unit_class="volume",
        name=f"AquaWatch {entry_title}",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement="m³",
    )

    ordered = sorted(records, key=lambda r: r.record_date)
    running_sum = running_sum_start
    statistics: list[StatisticData] = []
    for record in ordered:
        running_sum += record.liters / 1000
        statistics.append(
            StatisticData(
                start=datetime.combine(
                    record.record_date, datetime.min.time(), tzinfo=timezone.utc
                ),
                state=record.liters / 1000,
                sum=running_sum,
            )
        )

    async_add_external_statistics(hass, metadata, statistics)
    return running_sum
