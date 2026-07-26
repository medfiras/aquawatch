"""tests/test_button.py"""

from unittest.mock import AsyncMock, MagicMock

from custom_components.aquawatch.button import AquaWatchForceRefreshButton


async def test_async_press_requests_a_coordinator_refresh() -> None:
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    entity = AquaWatchForceRefreshButton.__new__(AquaWatchForceRefreshButton)
    entity.coordinator = coordinator

    await entity.async_press()

    coordinator.async_request_refresh.assert_awaited_once()


def test_button_has_entity_name_so_ha_does_not_prefix_device_name_per_row() -> None:
    entity = AquaWatchForceRefreshButton.__new__(AquaWatchForceRefreshButton)
    assert entity.has_entity_name is True
