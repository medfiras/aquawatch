"""Sensor platform for AquaWatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONTRACT_ID, CONF_CONTRACT_NUMBER, DOMAIN
from .coordinator import AquaWatchCoordinator, AquaWatchData


@dataclass(frozen=True, kw_only=True)
class AquaWatchSensorDescription(SensorEntityDescription):
    """Describes one AquaWatch sensor and how to compute its value."""

    value_fn: Callable[[AquaWatchData], float | int | str | None]


SENSOR_DESCRIPTIONS: tuple[AquaWatchSensorDescription, ...] = (
    AquaWatchSensorDescription(
        key="consommation_jour",
        translation_key="consommation_jour",
        native_unit_of_measurement="L",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.records[-1].liters if d.records else None,
    ),
    AquaWatchSensorDescription(
        key="consommation_veille",
        translation_key="consommation_veille",
        native_unit_of_measurement="L",
        device_class=SensorDeviceClass.WATER,
        value_fn=lambda d: d.records[-2].liters if len(d.records) >= 2 else None,
    ),
    AquaWatchSensorDescription(
        key="index_compteur",
        translation_key="index_compteur",
        native_unit_of_measurement="m³",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.records[-1].cumulative_index_m3 if d.records else None,
    ),
    AquaWatchSensorDescription(
        key="cout_jour",
        translation_key="cout_jour",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: (
            (d.records[-1].liters / 1000) * d.price_per_m3 if d.records else None
        ),
    ),
    AquaWatchSensorDescription(
        key="cout_mois_courant",
        translation_key="cout_mois_courant",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: d.cost_month_to_date,
    ),
    AquaWatchSensorDescription(
        key="prix_m3",
        translation_key="prix_m3",
        native_unit_of_measurement="EUR/m³",
        value_fn=lambda d: d.price_per_m3,
    ),
    AquaWatchSensorDescription(
        key="prevision_fin_mois_volume",
        translation_key="prevision_fin_mois_volume",
        native_unit_of_measurement="m³",
        device_class=SensorDeviceClass.WATER,
        value_fn=lambda d: d.forecast_volume_m3,
    ),
    AquaWatchSensorDescription(
        key="prevision_fin_mois_cout",
        translation_key="prevision_fin_mois_cout",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: d.forecast_cost,
    ),
    AquaWatchSensorDescription(
        key="vs_semaine_precedente",
        translation_key="vs_semaine_precedente",
        native_unit_of_measurement="%",
        value_fn=lambda d: d.vs_last_week_pct,
    ),
    AquaWatchSensorDescription(
        key="vs_mois_precedent",
        translation_key="vs_mois_precedent",
        native_unit_of_measurement="%",
        value_fn=lambda d: d.vs_last_month_pct,
    ),
    AquaWatchSensorDescription(
        key="vs_annee_precedente",
        translation_key="vs_annee_precedente",
        native_unit_of_measurement="%",
        value_fn=lambda d: d.vs_last_year_pct,
    ),
    AquaWatchSensorDescription(
        key="eco_score",
        translation_key="eco_score",
        native_unit_of_measurement="pts",
        value_fn=lambda d: d.eco_score,
    ),
    AquaWatchSensorDescription(
        key="derniere_synchro",
        translation_key="derniere_synchro",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_sync,
    ),
    AquaWatchSensorDescription(
        key="solde_compte",
        translation_key="solde_compte",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: d.account_balance,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up AquaWatch sensors from a config entry."""
    coordinator: AquaWatchCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AquaWatchSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class AquaWatchSensor(CoordinatorEntity[AquaWatchCoordinator], SensorEntity):
    """A single AquaWatch metric."""

    entity_description: AquaWatchSensorDescription
    # Without this, HA prefixes every entity's displayed name with the full
    # device name (the config entry title) -- with it, the device's own
    # entity list shows just the translated entity name (e.g. "Consommation
    # du jour"), and other views show "<device> <entity>" only once, not
    # per-row.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AquaWatchCoordinator,
        entry: ConfigEntry,
        description: AquaWatchSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        # Falls back to the opaque contract_id for entries created before
        # CONF_CONTRACT_NUMBER existed.
        self._contract_number = entry.data.get(
            CONF_CONTRACT_NUMBER, entry.data[CONF_CONTRACT_ID]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="AquaWatch",
        )

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.entity_description.key == "eco_score":
            return {
                "grade": self.coordinator.data.eco_grade,
                "conseil": self.coordinator.data.eco_tip,
            }
        if self.entity_description.key == "index_compteur":
            data = self.coordinator.data
            return {
                "numero_contrat": self._contract_number,
                "numero_serie_compteur": data.meter_serial_number,
                "adresse": data.site_address,
                "statut_contrat": data.contract_status,
            }
        return None
