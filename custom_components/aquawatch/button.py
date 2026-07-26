"""Button platform for AquaWatch."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AquaWatchCoordinator

FORCE_REFRESH_DESCRIPTION = ButtonEntityDescription(
    key="force_refresh",
    translation_key="force_refresh",
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the AquaWatch button from a config entry."""
    coordinator: AquaWatchCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AquaWatchForceRefreshButton(coordinator, entry)])


class AquaWatchForceRefreshButton(
    CoordinatorEntity[AquaWatchCoordinator], ButtonEntity
):
    """Button that forces an immediate data refresh."""

    entity_description = FORCE_REFRESH_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: AquaWatchCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{FORCE_REFRESH_DESCRIPTION.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
