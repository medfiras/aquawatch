"""tests/test_init.py"""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquawatch.const import (
    CONF_CONTRACT_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    DOMAIN,
)
from custom_components.aquawatch.models import ConsumptionBatch, ConsumptionRecord


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )


def _fake_batch() -> ConsumptionBatch:
    # The fake provider ignores the requested date range and always returns
    # this batch, so its record is dated "today" to mirror how a real
    # backfill's window always ends at today — this is what lets the
    # coordinator's first-refresh incremental window end up empty once it's
    # seeded from the backfill.
    return ConsumptionBatch(
        records=[ConsumptionRecord(date.today(), 150.0, 100.0, False)],
        price_per_m3=4.0,
    )


async def test_setup_entry_backfills_statistics_and_creates_coordinator(hass) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    fake_provider = AsyncMock()
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=_fake_batch())
    fake_provider.async_close = AsyncMock()
    fake_provider_cls = lambda: fake_provider

    with (
        patch(
            "custom_components.aquawatch.get_provider_class",
            return_value=fake_provider_cls,
        ),
        patch(
            "custom_components.aquawatch.get_last_statistics",
            return_value={},
        ),
        patch(
            "custom_components.aquawatch.statistics.async_add_external_statistics"
        ) as mock_add_stats,
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=fake_provider_cls,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={},
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state.value == "loaded"
    assert DOMAIN in hass.data
    assert entry.entry_id in hass.data[DOMAIN]
    # The backfill fetches through "today" and pushes its records into
    # long-term statistics. The coordinator is then seeded with those same
    # records/running-sum (see __init__.async_setup_entry ->
    # coordinator.seed_from_backfill), so its first refresh's incremental
    # window (last_known + 1 day .. today) is empty and it pushes NOTHING
    # further — there must be exactly one push of the backfilled records,
    # not two, or the statistic's cumulative sum would double-count the
    # overlapping days.
    assert mock_add_stats.call_count == 1
    pushed_statistics = mock_add_stats.call_args_list[0].args[2]
    pushed_dates = {stat["start"].date() for stat in pushed_statistics}
    assert pushed_dates == {record.record_date for record in _fake_batch().records}

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._records == sorted(
        _fake_batch().records, key=lambda r: r.record_date
    )
    assert coordinator._running_sum == pytest.approx(
        sum(r.liters for r in _fake_batch().records) / 1000
    )


async def test_unload_entry_removes_coordinator(hass) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    fake_provider = AsyncMock()
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=_fake_batch())
    fake_provider.async_close = AsyncMock()
    fake_provider_cls = lambda: fake_provider

    with (
        patch(
            "custom_components.aquawatch.get_provider_class",
            return_value=fake_provider_cls,
        ),
        patch("custom_components.aquawatch.get_last_statistics", return_value={}),
        patch("custom_components.aquawatch.statistics.async_add_external_statistics"),
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=fake_provider_cls,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
