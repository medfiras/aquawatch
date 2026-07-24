"""tests/test_sensor.py"""

from datetime import date, datetime
from unittest.mock import MagicMock

from custom_components.aquawatch.coordinator import AquaWatchData
from custom_components.aquawatch.models import ConsumptionRecord
from custom_components.aquawatch.sensor import SENSOR_DESCRIPTIONS, AquaWatchSensor


def _fake_coordinator(data: AquaWatchData) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def _sample_data() -> AquaWatchData:
    return AquaWatchData(
        records=[
            ConsumptionRecord(date(2024, 3, 14), 140.0, 99.86, False),
            ConsumptionRecord(date(2024, 3, 15), 150.0, 100.0, False),
        ],
        price_per_m3=4.0,
        last_sync=datetime(2024, 3, 15, 6, 0),
        forecast_volume_m3=6.0,
        forecast_cost=24.0,
        eco_score=75,
        eco_grade="B",
        eco_tip="Bonne consommation.",
        vs_last_week_pct=10.0,
        vs_last_month_pct=-5.0,
        vs_last_year_pct=2.0,
        leak_suspected=False,
        anomaly_detected=False,
        budget_exceeded=False,
        data_stale=False,
    )


def _find_description(key: str):
    return next(d for d in SENSOR_DESCRIPTIONS if d.key == key)


def test_consommation_jour_reads_last_record() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("consommation_jour")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    assert entity.native_value == 150.0


def test_index_compteur_reads_cumulative_index() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("index_compteur")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    assert entity.native_value == 100.0


def test_cout_jour_multiplies_liters_by_price() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("cout_jour")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    assert entity.native_value == 0.6


def test_eco_score_exposes_grade_and_tip_attributes() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("eco_score")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    assert entity.native_value == 75
    assert entity.extra_state_attributes == {
        "grade": "B",
        "conseil": "Bonne consommation.",
    }


def test_all_descriptions_have_unique_keys() -> None:
    keys = [d.key for d in SENSOR_DESCRIPTIONS]
    assert len(keys) == len(set(keys))
