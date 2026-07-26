"""The AquaWatch integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from . import statistics
from .const import CONF_CONTRACT_ID, CONF_EMAIL, CONF_PASSWORD, CONF_PROVIDER, DOMAIN
from .coordinator import (
    _HISTORY_WINDOW_DAYS,
    AquaWatchCoordinator,
    async_fetch_with_shrinking_window,
)
from .models import ConsumptionBatch, ConsumptionRecord
from .providers import get_provider_class
from .providers.exceptions import AuthError, ProviderError
from .services import async_setup_services, async_unload_services

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AquaWatch from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    statistic_id = statistics.statistic_id_for_entry(entry.entry_id)
    cost_statistic_id = statistics.cost_statistic_id_for_entry(entry.entry_id)
    last_stats = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    backfill_batch = None
    if not last_stats:
        backfill_batch = await _async_backfill_statistics(
            hass, entry, statistic_id, cost_statistic_id
        )
    else:
        # Volume history already exists (e.g. upgrading an install that
        # predates cost tracking), but the cost statistic itself may never
        # have been backfilled -- _async_backfill_statistics above is the
        # only place that pushes it, and it's skipped whenever volume
        # already has data. Without this, cost would only start
        # accumulating from whatever day the coordinator's own update cycle
        # first ran after the upgrade, leaving every earlier day at 0 in
        # the Energy dashboard.
        last_cost_stats = await hass.async_add_executor_job(
            get_last_statistics, hass, 1, cost_statistic_id, True, {"sum"}
        )
        if not last_cost_stats:
            await _async_backfill_cost_from_existing_volume(
                hass, entry, statistic_id, cost_statistic_id
            )

    coordinator = AquaWatchCoordinator(hass, entry)
    if backfill_batch and backfill_batch.records:
        # Seed the coordinator with what the backfill already fetched and
        # pushed, so its first refresh only fetches/pushes days AFTER the
        # backfill's window instead of re-fetching and re-pushing the same
        # days into long-term statistics (which would corrupt the running
        # sum). running_sum_start=0.0 was used for the backfill's push, so
        # the resulting running sum is simply the sum of all backfilled
        # records' liters (in m3).
        running_sum = sum(r.liters for r in backfill_batch.records) / 1000
        cost_running_sum = running_sum * backfill_batch.price_per_m3
        coordinator.seed_from_backfill(
            backfill_batch.records, running_sum, cost_running_sum
        )
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
        if not hass.data[DOMAIN]:
            async_unload_services(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the durable external statistic when the entry is fully removed.

    Without this, the statistics pushed by `statistics.async_push_records`
    and `async_push_cost_records` stay in the recorder forever under their
    entry_id-derived statistic_ids, forever showing up as same-named
    duplicates ("AquaWatch <title>") in the Energy dashboard's source
    picker every time the integration is removed and re-added.
    """
    statistic_id = statistics.statistic_id_for_entry(entry.entry_id)
    cost_statistic_id = statistics.cost_statistic_id_for_entry(entry.entry_id)
    get_instance(hass).async_clear_statistics([statistic_id, cost_statistic_id])


async def _async_backfill_statistics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    statistic_id: str,
    cost_statistic_id: str,
) -> ConsumptionBatch | None:
    """Import as much historical consumption as the provider will allow.

    Pushes the fetched records into long-term statistics (this is the ONE
    authoritative push for the historical window) and returns the batch so
    the caller can seed the coordinator's in-memory state with it, letting
    the coordinator's first refresh continue from where this backfill left
    off instead of re-fetching/re-pushing the same days.
    """
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

        try:
            batch = await async_fetch_with_shrinking_window(
                provider, entry.data[CONF_CONTRACT_ID], today
            )
        except ProviderError:
            # Every window failed (e.g. this account has less history than
            # even the smallest fallback). Leave `batch` as None so setup
            # still proceeds without a historical backfill -- the
            # coordinator's own first refresh will surface a proper repair
            # issue if its own (equally shrinking-window) attempt fails too.
            pass
    finally:
        await provider.async_close()

    if not batch or not batch.records:
        return None

    statistics.async_push_records(
        hass, statistic_id, entry.title, batch.records, running_sum_start=0.0
    )
    statistics.async_push_cost_records(
        hass,
        cost_statistic_id,
        entry.title,
        batch.records,
        batch.price_per_m3,
        running_sum_start=0.0,
    )
    return batch


async def _async_backfill_cost_from_existing_volume(
    hass: HomeAssistant,
    entry: ConfigEntry,
    statistic_id: str,
    cost_statistic_id: str,
) -> None:
    """Retroactively backfill the cost statistic from the volume statistic's
    own history.

    Handles upgrading an existing installation to a version with cost
    tracking: the volume statistic may already hold years of daily history,
    but the cost statistic has never been pushed (see the caller). The
    exact historical price isn't retained anywhere, so every past day is
    costed at today's rate -- the same blended-price simplification already
    used everywhere else in this integration.

    Best-effort: any failure here must not block setup, since the volume
    statistic and the coordinator's own ongoing cost tracking are unaffected
    either way.
    """
    provider_cls = get_provider_class(entry.data[CONF_PROVIDER])
    provider = provider_cls()
    today = date.today()
    price_per_m3 = None
    try:
        try:
            await provider.async_authenticate(
                entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
            )
        except AuthError:
            return
        try:
            recent_batch = await provider.async_get_daily_consumption(
                entry.data[CONF_CONTRACT_ID], today - timedelta(days=1), today
            )
            price_per_m3 = recent_batch.price_per_m3
        except ProviderError:
            return
    finally:
        await provider.async_close()

    if not price_per_m3:
        return

    start_time = datetime.combine(
        today - timedelta(days=_HISTORY_WINDOW_DAYS),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    volume_rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start_time,
        None,
        {statistic_id},
        "day",
        None,
        {"state"},
    )
    records = []
    for row in volume_rows.get(statistic_id, []):
        row_start = row.get("start")
        state = row.get("state")
        if row_start is None or state is None:
            continue
        records.append(
            ConsumptionRecord(
                record_date=datetime.fromtimestamp(row_start, tz=timezone.utc).date(),
                liters=state * 1000,
                cumulative_index_m3=0.0,
                is_estimated=True,
            )
        )

    statistics.async_push_cost_records(
        hass, cost_statistic_id, entry.title, records, price_per_m3, running_sum_start=0.0
    )
