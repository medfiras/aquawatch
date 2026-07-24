"""tests/test_services.py"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.aquawatch.services as services
from custom_components.aquawatch.const import (
    DOMAIN,
    SERVICE_EXPORT_CSV,
    SERVICE_FORCE_REFRESH,
    SERVICE_RECALIBRATE_BASELINE,
)
from custom_components.aquawatch.coordinator import AquaWatchData
from custom_components.aquawatch.models import ConsumptionRecord
from custom_components.aquawatch.services import async_setup_services


@pytest.fixture(autouse=True)
def _reset_services_registered():
    """Each test uses a fresh `hass` fixture, so the module-level
    registration guard in services.py must be reset per test, otherwise
    later tests silently skip registration against their own hass instance.
    """
    services._services_registered = False
    yield
    services._services_registered = False


def _fake_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_recalibrate_baseline = AsyncMock()
    coordinator.data = AquaWatchData(
        records=[ConsumptionRecord(date(2024, 3, 15), 150.0, 100.0, False)],
        price_per_m3=4.0,
        last_sync=None,
        forecast_volume_m3=None,
        forecast_cost=None,
        eco_score=50,
        eco_grade="D",
        eco_tip="",
        vs_last_week_pct=None,
        vs_last_month_pct=None,
        vs_last_year_pct=None,
        leak_suspected=False,
        anomaly_detected=False,
        budget_exceeded=False,
        data_stale=False,
    )
    return coordinator


async def test_force_refresh_calls_coordinator(hass) -> None:
    coordinator = _fake_coordinator()
    hass.data[DOMAIN] = {"entry-1": coordinator}
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_FORCE_REFRESH, {"entry_id": "entry-1"}, blocking=True
    )

    coordinator.async_request_refresh.assert_awaited_once()


async def test_recalibrate_baseline_calls_coordinator(hass) -> None:
    coordinator = _fake_coordinator()
    hass.data[DOMAIN] = {"entry-1": coordinator}
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_RECALIBRATE_BASELINE, {"entry_id": "entry-1"}, blocking=True
    )

    coordinator.async_recalibrate_baseline.assert_awaited_once()


async def test_export_csv_returns_csv_response(hass) -> None:
    coordinator = _fake_coordinator()
    hass.data[DOMAIN] = {"entry-1": coordinator}
    async_setup_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_EXPORT_CSV,
        {"entry_id": "entry-1"},
        blocking=True,
        return_response=True,
    )

    assert "date,liters,cumulative_index_m3,is_estimated" in response["csv"]
    assert "2024-03-15,150.0,100.0,False" in response["csv"]


async def test_unknown_entry_id_raises(hass) -> None:
    hass.data[DOMAIN] = {}
    async_setup_services(hass)

    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN, SERVICE_FORCE_REFRESH, {"entry_id": "missing"}, blocking=True
        )
