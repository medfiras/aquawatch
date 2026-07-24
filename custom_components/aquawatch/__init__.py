"""The AquaWatch integration."""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from . import statistics
from .const import CONF_CONTRACT_ID, CONF_EMAIL, CONF_PASSWORD, CONF_PROVIDER, DOMAIN
from .coordinator import AquaWatchCoordinator
from .providers import get_provider_class
from .providers.exceptions import AuthError, ProviderError
from .services import async_setup_services

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

_BACKFILL_ATTEMPTS_DAYS = (365 * 3, 365 * 2, 365, 180)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AquaWatch from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    statistic_id = statistics.statistic_id_for_entry(entry.entry_id)
    last_stats = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    if not last_stats:
        await _async_backfill_statistics(hass, entry, statistic_id)

    coordinator = AquaWatchCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an AquaWatch config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_backfill_statistics(
    hass: HomeAssistant, entry: ConfigEntry, statistic_id: str
) -> None:
    """Import as much historical consumption as the provider will allow."""
    provider_cls = get_provider_class(entry.data[CONF_PROVIDER])
    provider = provider_cls()
    today = date.today()
    batch = None
    try:
        try:
            await provider.async_authenticate(
                entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
            )
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err

        for days_back in _BACKFILL_ATTEMPTS_DAYS:
            try:
                batch = await provider.async_get_daily_consumption(
                    entry.data[CONF_CONTRACT_ID],
                    today - timedelta(days=days_back),
                    today,
                )
                break
            except ProviderError:
                continue
    finally:
        await provider.async_close()

    if not batch or not batch.records:
        return

    statistics.async_push_records(
        hass, statistic_id, entry.title, batch.records, running_sum_start=0.0
    )
