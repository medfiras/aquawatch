"""tests/test_diagnostics.py"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquawatch.const import (
    CONF_CONTRACT_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    DOMAIN,
)
from custom_components.aquawatch.coordinator import AquaWatchData
from custom_components.aquawatch.diagnostics import async_get_config_entry_diagnostics
from custom_components.aquawatch.models import ConsumptionRecord


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "super-secret",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _seed_coordinator(hass, entry: MockConfigEntry) -> None:
    coordinator = MagicMock()
    coordinator.data = AquaWatchData(
        records=[ConsumptionRecord(date(2024, 3, 15), 150.0, 100.0, False)],
        price_per_m3=4.0,
        last_sync=datetime(2024, 3, 15, 6, 0),
        forecast_volume_m3=6.0,
        forecast_cost=24.0,
        eco_score=75,
        eco_grade="B",
        eco_tip="",
        vs_last_week_pct=None,
        vs_last_month_pct=None,
        vs_last_year_pct=None,
        leak_suspected=False,
        anomaly_detected=False,
        budget_exceeded=False,
        data_stale=False,
        cost_month_to_date=9.0,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator


async def test_diagnostics_redacts_credentials(hass) -> None:
    entry = _entry(hass)
    _seed_coordinator(hass, entry)

    fake_provider = AsyncMock()
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_raw_contract_details = AsyncMock(return_value={})
    fake_provider.async_close = AsyncMock()

    with patch(
        "custom_components.aquawatch.diagnostics.get_provider_class",
        return_value=lambda: fake_provider,
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry_data"][CONF_EMAIL] == "**REDACTED**"
    assert diagnostics["entry_data"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["record_count"] == 1
    assert diagnostics["computed"]["eco_score"] == 75
    assert diagnostics["computed"]["eco_grade"] == "B"
    assert diagnostics["computed"]["last_sync"] == "2024-03-15T06:00:00"
    assert diagnostics["computed"]["forecast_volume_m3"] == 6.0
    assert diagnostics["computed"]["forecast_cost"] == 24.0
    assert diagnostics["computed"]["cost_month_to_date"] == 9.0


async def test_diagnostics_includes_raw_contract_details_on_success(hass) -> None:
    entry = _entry(hass)
    _seed_coordinator(hass, entry)

    raw_response = {"numeroContrat": "9257681", "compteInfo": [{"ELEMB": "X", "ELEMA": "Y"}]}
    fake_provider = AsyncMock()
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_raw_contract_details = AsyncMock(return_value=raw_response)
    fake_provider.async_close = AsyncMock()

    with patch(
        "custom_components.aquawatch.diagnostics.get_provider_class",
        return_value=lambda: fake_provider,
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["debug_raw_contract_details"] == raw_response
    fake_provider.async_get_raw_contract_details.assert_awaited_once_with("CTR-1")
    fake_provider.async_close.assert_awaited_once()


async def test_diagnostics_reports_error_string_instead_of_raising(hass) -> None:
    entry = _entry(hass)
    _seed_coordinator(hass, entry)

    fake_provider = AsyncMock()
    fake_provider.async_authenticate = AsyncMock(side_effect=RuntimeError("boom"))
    fake_provider.async_close = AsyncMock()

    with patch(
        "custom_components.aquawatch.diagnostics.get_provider_class",
        return_value=lambda: fake_provider,
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert "error fetching raw contract details" in diagnostics["debug_raw_contract_details"]
    assert "boom" in diagnostics["debug_raw_contract_details"]
    fake_provider.async_close.assert_awaited_once()


async def test_diagnostics_reports_unsupported_for_providers_without_the_method(
    hass,
) -> None:
    entry = _entry(hass)
    _seed_coordinator(hass, entry)

    class _MinimalProvider:
        async def async_close(self):
            return None

    fake_provider = _MinimalProvider()

    with patch(
        "custom_components.aquawatch.diagnostics.get_provider_class",
        return_value=lambda: fake_provider,
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["debug_raw_contract_details"] == "not supported by this provider"
