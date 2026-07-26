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
        cost_month_to_date=9.0,
        account_balance=-12.5,
        contract_status="Actif",
        site_address="85 AV DE VERSAILLES, 93220 GAGNY",
        meter_serial_number="I26IA206176",
        cost_total=123.45,
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


def test_cout_mois_courant_and_prevision_fin_mois_cout_can_differ() -> None:
    """The month-to-date accrued cost and the end-of-month forecast are distinct metrics."""
    data = _sample_data()
    assert data.cost_month_to_date != data.forecast_cost

    coordinator = _fake_coordinator(data)

    cout_mois_courant = AquaWatchSensor.__new__(AquaWatchSensor)
    cout_mois_courant.coordinator = coordinator
    cout_mois_courant.entity_description = _find_description("cout_mois_courant")

    prevision_fin_mois_cout = AquaWatchSensor.__new__(AquaWatchSensor)
    prevision_fin_mois_cout.coordinator = coordinator
    prevision_fin_mois_cout.entity_description = _find_description("prevision_fin_mois_cout")

    assert cout_mois_courant.native_value == 9.0
    assert prevision_fin_mois_cout.native_value == 24.0
    assert cout_mois_courant.native_value != prevision_fin_mois_cout.native_value


def test_sensor_has_entity_name_so_ha_does_not_prefix_device_name_per_row() -> None:
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    assert entity.has_entity_name is True


def test_index_compteur_exposes_contract_and_meter_attributes() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("index_compteur")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    entity._contract_number = "Contrat 9257681"
    assert entity.extra_state_attributes == {
        "numero_contrat": "Contrat 9257681",
        "numero_serie_compteur": "I26IA206176",
        "adresse": "85 AV DE VERSAILLES, 93220 GAGNY",
        "statut_contrat": "Actif",
    }


def test_solde_compte_reads_account_balance() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("solde_compte")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    assert entity.native_value == -12.5


def test_cout_total_reads_cost_total() -> None:
    coordinator = _fake_coordinator(_sample_data())
    description = _find_description("cout_total")
    entity = AquaWatchSensor.__new__(AquaWatchSensor)
    entity.coordinator = coordinator
    entity.entity_description = description
    assert entity.native_value == 123.45


def test_init_uses_contract_number_when_present() -> None:
    from types import SimpleNamespace

    from custom_components.aquawatch.const import CONF_CONTRACT_ID, CONF_CONTRACT_NUMBER

    entry = SimpleNamespace(
        entry_id="entry1",
        title="L'Eau d'Île-de-France (SEDIF) — Contrat 9257681",
        data={CONF_CONTRACT_ID: "opaque-id", CONF_CONTRACT_NUMBER: "Contrat 9257681"},
    )
    description = _find_description("index_compteur")
    entity = AquaWatchSensor(_fake_coordinator(_sample_data()), entry, description)
    assert entity._contract_number == "Contrat 9257681"


def test_init_falls_back_to_contract_id_without_contract_number() -> None:
    from types import SimpleNamespace

    from custom_components.aquawatch.const import CONF_CONTRACT_ID

    entry = SimpleNamespace(
        entry_id="entry1",
        title="L'Eau d'Île-de-France (SEDIF)",
        data={CONF_CONTRACT_ID: "opaque-id"},
    )
    description = _find_description("index_compteur")
    entity = AquaWatchSensor(_fake_coordinator(_sample_data()), entry, description)
    assert entity._contract_number == "opaque-id"
