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
    """Return the external statistic_id used for a given config entry.

    HA's valid_statistic_id() only allows lowercase [0-9a-z_] in either half
    of the "<domain>:<object_id>" format. entry_id is a ULID (Crockford
    Base32), which is uppercase, so it must be lowercased here.
    """
    return f"{DOMAIN}:{entry_id.lower()}_consumption"


def cost_statistic_id_for_entry(entry_id: str) -> str:
    """Return the external cost statistic_id used for a given config entry.

    A separate statistic from `statistic_id_for_entry`'s volume one -- the
    Energy dashboard's water source dialog only allows picking a price
    entity/fixed price for a *regular* (entity-backed) consumption source;
    for an external statistic like ours, it requires a dedicated cumulative
    cost statistic instead (see `ha-radio-option .disabled=${externalSource}`
    in dialog-energy-water-settings.ts).
    """
    return f"{DOMAIN}:{entry_id.lower()}_cost"


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


def async_push_cost_records(
    hass: HomeAssistant,
    statistic_id: str,
    entry_title: str,
    records: list[ConsumptionRecord],
    price_per_m3: float,
    running_sum_start: float,
) -> float:
    """Push each record's cost (priced at `price_per_m3`) into long-term
    statistics, returning the updated running sum.

    Each record is costed at the price in effect when it was fetched
    (`price_per_m3`, the same blended average SEDIF reports for the whole
    queried range) -- once pushed, a day's cost is never retroactively
    recalculated if the tariff changes later, same append-only guarantee as
    the volume statistic.
    """
    if not records:
        return running_sum_start

    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
        name=f"AquaWatch {entry_title} (coût)",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement="EUR",
    )

    ordered = sorted(records, key=lambda r: r.record_date)
    running_sum = running_sum_start
    statistics: list[StatisticData] = []
    for record in ordered:
        running_sum += (record.liters / 1000) * price_per_m3
        statistics.append(
            StatisticData(
                start=datetime.combine(
                    record.record_date, datetime.min.time(), tzinfo=timezone.utc
                ),
                state=(record.liters / 1000) * price_per_m3,
                sum=running_sum,
            )
        )

    async_add_external_statistics(hass, metadata, statistics)
    return running_sum
