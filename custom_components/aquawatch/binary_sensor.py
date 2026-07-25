"""Binary sensor platform for AquaWatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AquaWatchCoordinator, AquaWatchData


@dataclass(frozen=True, kw_only=True)
class AquaWatchBinarySensorDescription(BinarySensorEntityDescription):
    """Describes one AquaWatch binary sensor and how to compute its state."""

    value_fn: Callable[[AquaWatchData], bool]


BINARY_SENSOR_DESCRIPTIONS: tuple[AquaWatchBinarySensorDescription, ...] = (
    AquaWatchBinarySensorDescription(
        key="fuite_suspectee",
        translation_key="fuite_suspectee",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda d: d.leak_suspected,
    ),
    AquaWatchBinarySensorDescription(
        key="anomalie_detectee",
        translation_key="anomalie_detectee",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.anomaly_detected,
    ),
    AquaWatchBinarySensorDescription(
        key="budget_depasse",
        translation_key="budget_depasse",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.budget_exceeded,
    ),
    AquaWatchBinarySensorDescription(
        key="donnees_perimees",
        translation_key="donnees_perimees",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.data_stale,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up AquaWatch binary sensors from a config entry."""
    coordinator: AquaWatchCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AquaWatchBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class AquaWatchBinarySensor(
    CoordinatorEntity[AquaWatchCoordinator], BinarySensorEntity
):
    """A single AquaWatch alert condition."""

    entity_description: AquaWatchBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AquaWatchCoordinator,
        entry: ConfigEntry,
        description: AquaWatchBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)
