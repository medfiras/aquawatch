"""tests/test_diagnostics.py"""

from datetime import date, datetime
from unittest.mock import MagicMock

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


async def test_diagnostics_redacts_credentials(hass) -> None:
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
        account_balance=None,
        contract_status=None,
        site_address=None,
        meter_serial_number=None,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

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
