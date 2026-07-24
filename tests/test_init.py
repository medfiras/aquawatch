"""tests/test_init.py"""

from datetime import date
from unittest.mock import AsyncMock, patch

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
    return ConsumptionBatch(
        records=[ConsumptionRecord(date(2024, 3, 15), 150.0, 100.0, False)],
        price_per_m3=4.0,
    )


async def test_setup_entry_backfills_statistics_and_creates_coordinator(hass) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    fake_provider = AsyncMock()
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
    # Called twice: once by the initial backfill, once by the coordinator's
    # first refresh (which also pushes newly-fetched records into long-term
    # statistics — see coordinator.py). The fake provider returns the same
    # fixed batch regardless of the requested date range, so both calls carry
    # the same record here; in production the ranges (and hence records)
    # differ so nothing is double-counted.
    assert mock_add_stats.call_count == 2


async def test_unload_entry_removes_coordinator(hass) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    fake_provider = AsyncMock()
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
