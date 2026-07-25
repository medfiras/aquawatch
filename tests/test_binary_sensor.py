"""tests/test_binary_sensor.py"""

from datetime import datetime
from unittest.mock import MagicMock

from custom_components.aquawatch.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    AquaWatchBinarySensor,
)
from custom_components.aquawatch.coordinator import AquaWatchData


def _fake_coordinator(**overrides) -> MagicMock:
    base = dict(
        records=[],
        price_per_m3=4.0,
        last_sync=datetime(2024, 3, 15, 6, 0),
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
        cost_month_to_date=None,
        account_balance=None,
        contract_status=None,
        site_address=None,
        meter_serial_number=None,
    )
    base.update(overrides)
    coordinator = MagicMock()
    coordinator.data = AquaWatchData(**base)
    return coordinator


def _find_description(key: str):
    return next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == key)


def test_fuite_suspectee_reflects_leak_suspected() -> None:
    coordinator = _fake_coordinator(leak_suspected=True)
    entity = AquaWatchBinarySensor.__new__(AquaWatchBinarySensor)
    entity.coordinator = coordinator
    entity.entity_description = _find_description("fuite_suspectee")
    assert entity.is_on is True


def test_anomalie_detectee_reflects_anomaly_detected() -> None:
    coordinator = _fake_coordinator(anomaly_detected=True)
    entity = AquaWatchBinarySensor.__new__(AquaWatchBinarySensor)
    entity.coordinator = coordinator
    entity.entity_description = _find_description("anomalie_detectee")
    assert entity.is_on is True


def test_budget_depasse_reflects_budget_exceeded() -> None:
    coordinator = _fake_coordinator(budget_exceeded=True)
    entity = AquaWatchBinarySensor.__new__(AquaWatchBinarySensor)
    entity.coordinator = coordinator
    entity.entity_description = _find_description("budget_depasse")
    assert entity.is_on is True


def test_donnees_perimees_reflects_data_stale() -> None:
    coordinator = _fake_coordinator(data_stale=True)
    entity = AquaWatchBinarySensor.__new__(AquaWatchBinarySensor)
    entity.coordinator = coordinator
    entity.entity_description = _find_description("donnees_perimees")
    assert entity.is_on is True


def test_all_off_by_default() -> None:
    coordinator = _fake_coordinator()
    for description in BINARY_SENSOR_DESCRIPTIONS:
        entity = AquaWatchBinarySensor.__new__(AquaWatchBinarySensor)
        entity.coordinator = coordinator
        entity.entity_description = description
        assert entity.is_on is False


def test_binary_sensor_has_entity_name_so_ha_does_not_prefix_device_name_per_row() -> None:
    entity = AquaWatchBinarySensor.__new__(AquaWatchBinarySensor)
    assert entity.has_entity_name is True
