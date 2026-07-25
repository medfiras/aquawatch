"""DataUpdateCoordinator for AquaWatch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import statistics
from .const import (
    BUDGET_UNIT_EUR,
    CONF_CONTRACT_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    DEFAULT_ANOMALY_ZSCORE_THRESHOLD,
    DEFAULT_HOUSEHOLD_SIZE,
    DEFAULT_LEAK_CONSECUTIVE_DAYS,
    DEFAULT_LEAK_THRESHOLD_RATIO,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    EVENT_ANOMALY_DETECTED,
    EVENT_BUDGET_EXCEEDED,
    EVENT_LEAK_SUSPECTED,
    OPT_ANOMALY_ZSCORE_THRESHOLD,
    OPT_BUDGET_AMOUNT,
    OPT_BUDGET_UNIT,
    OPT_HOUSEHOLD_SIZE,
    OPT_LEAK_CONSECUTIVE_DAYS,
    OPT_LEAK_THRESHOLD_RATIO,
    OPT_UPDATE_INTERVAL_HOURS,
)
from .detection import detect_statistical_anomaly, detect_sustained_leak
from .ecoscore import compute_eco_score
from .forecast import forecast_month_end_cost, forecast_month_end_volume_m3
from .models import ConsumptionBatch, ConsumptionRecord
from .providers import get_provider_class
from .providers.exceptions import AuthError, ProviderError, ScrapingError
from .repairs import async_create_scraping_broken_issue
from .statistics import statistic_id_for_entry

_LOGGER = logging.getLogger(__name__)

_HISTORY_WINDOW_DAYS = 740
_BASELINE_DAYS = 14
_STALE_AFTER_DAYS = 3

# SEDIF's backend raises a server-side System.NullPointerException instead
# of a graceful empty/partial response when a requested date range predates
# the data actually available for a given account -- confirmed empirically
# against two different real accounts, at two different code paths
# (LTN015_ICL_ContratConsoHisto.getData itself, and its
# convertConsommationToWrapper helper). Exactly how far back an account's
# data goes varies per account/meter and cannot be known in advance, so
# both the coordinator's cold-start fetch and the one-time historical
# backfill (see __init__.py) retry with progressively smaller windows
# instead of assuming a single fixed cutoff.
COLD_START_ATTEMPTS_DAYS = (365 * 2, 365, 180, 90, 30, 7)


async def async_fetch_with_shrinking_window(
    provider: "WaterProvider",  # noqa: F821 - see providers/__init__.py
    contract_id: str,
    today: date,
    attempts_days: tuple[int, ...] = COLD_START_ATTEMPTS_DAYS,
) -> ConsumptionBatch:
    """Fetch history, retrying with shrinking windows on ProviderError.

    Tries each window in `attempts_days` (largest first) until one
    succeeds. Raises the LAST attempt's error if all of them fail.
    """
    last_error: ProviderError | None = None
    for days_back in attempts_days:
        try:
            return await provider.async_get_daily_consumption(
                contract_id, today - timedelta(days=days_back), today
            )
        except ProviderError as err:
            last_error = err
            continue
    assert last_error is not None  # attempts_days is never empty
    raise last_error


@dataclass
class AquaWatchData:
    """Computed state exposed to AquaWatch entities."""

    records: list[ConsumptionRecord]
    price_per_m3: float
    last_sync: datetime
    forecast_volume_m3: float | None
    forecast_cost: float | None
    eco_score: int
    eco_grade: str
    eco_tip: str
    vs_last_week_pct: float | None
    vs_last_month_pct: float | None
    vs_last_year_pct: float | None
    leak_suspected: bool
    anomaly_detected: bool
    budget_exceeded: bool
    data_stale: bool
    cost_month_to_date: float | None


class AquaWatchCoordinator(DataUpdateCoordinator[AquaWatchData]):
    """Fetch and process AquaWatch data on a schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval_hours = entry.options.get(
            OPT_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"aquawatch_{entry.entry_id}",
            update_interval=timedelta(hours=interval_hours),
        )
        self.entry = entry
        self._provider_cls = get_provider_class(entry.data[CONF_PROVIDER])
        self._contract_id = entry.data[CONF_CONTRACT_ID]
        self._records: list[ConsumptionRecord] = []
        self._statistic_id = statistic_id_for_entry(entry.entry_id)
        self._running_sum: float | None = None
        # The last date already durably covered by long-term statistics, as
        # recorded by the recorder component itself. Unlike `self._records`
        # (in-memory only), this reflects state that survives a HA restart,
        # so it is what protects against re-fetching/re-pushing already
        # counted days after every restart. `None` means "not yet determined
        # this coordinator lifetime" (lazily resolved on first update, same
        # as `self._running_sum`).
        self._last_statistic_date: date | None = None

    async def _async_update_data(self) -> AquaWatchData:
        provider = self._provider_cls()
        try:
            try:
                await provider.async_authenticate(
                    self.entry.data[CONF_EMAIL], self.entry.data[CONF_PASSWORD]
                )
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err

            today = datetime.now().date()

            # Provisional check for whether there is anything left to fetch,
            # based only on in-memory records (which do NOT survive a
            # restart). This is intentionally the same cheap gate as before;
            # it only decides whether it's worth consulting the durable
            # statistic below. The actual fetch start date is recomputed
            # afterwards from the durable statistic so it is correct even
            # right after a restart, when `self._records` is empty.
            if not self._records:
                provisional_start = today - timedelta(days=COLD_START_ATTEMPTS_DAYS[0])
            else:
                last_known = max(r.record_date for r in self._records)
                provisional_start = last_known + timedelta(days=1)

            price_per_m3 = self.data.price_per_m3 if self.data else 0.0
            if provisional_start <= today:
                if self._running_sum is None:
                    last_stats = await self.hass.async_add_executor_job(
                        get_last_statistics, self.hass, 1, self._statistic_id, True, {"sum"}
                    )
                    stats_for_id = last_stats.get(self._statistic_id) if last_stats else None
                    if stats_for_id:
                        self._running_sum = stats_for_id[0].get("sum") or 0.0
                        last_start = stats_for_id[0].get("start")
                        if last_start is not None:
                            self._last_statistic_date = datetime.fromtimestamp(
                                last_start, tz=timezone.utc
                            ).date()
                    else:
                        self._running_sum = 0.0

                # The date range already durably covered by long-term
                # statistics always wins over `self._records` (which resets
                # to empty on every restart), while still accounting for
                # records that are newer than the last pushed statistic
                # (e.g. right after `seed_from_backfill`, before this
                # lifetime's first push).
                if self._last_statistic_date is None:
                    start = provisional_start
                else:
                    candidates = [self._last_statistic_date]
                    if self._records:
                        candidates.append(max(r.record_date for r in self._records))
                    start = max(candidates) + timedelta(days=1)

                if start <= today:
                    batch = None
                    if self._last_statistic_date is None:
                        # True cold start (no durable anchor yet): the
                        # exact amount of history SEDIF will actually
                        # serve for this account is unknown in advance,
                        # so retry with shrinking windows. A ScrapingError
                        # surviving every attempt here is a genuine signal
                        # something is wrong (portal broken, or an account
                        # with no data at all), so it still creates the
                        # repair issue.
                        try:
                            batch = await async_fetch_with_shrinking_window(
                                provider, self._contract_id, today
                            )
                        except ScrapingError as err:
                            async_create_scraping_broken_issue(
                                self.hass, self.entry.entry_id
                            )
                            raise UpdateFailed(str(err)) from err
                        except ProviderError as err:
                            raise UpdateFailed(str(err)) from err
                    else:
                        # Steady-state incremental fetch (usually just
                        # "yesterday+1 .. today", i.e. today's reading).
                        # SEDIF's backend has been observed to raise the
                        # same NullPointerException here when it simply
                        # hasn't published a reading for the requested day
                        # yet (a normal, recurring reporting lag), not a
                        # structural break -- so this one case is treated
                        # as "nothing new yet" rather than a repair-worthy
                        # failure. If the portal is genuinely broken, the
                        # existing "donnees_perimees" staleness check will
                        # flag it once this persists for several days, and
                        # any non-Scraping ProviderError still surfaces.
                        try:
                            batch = await provider.async_get_daily_consumption(
                                self._contract_id, start, today
                            )
                        except ScrapingError:
                            _LOGGER.debug(
                                "Incremental fetch for %s..%s returned no data "
                                "(likely not yet published); will retry next cycle",
                                start,
                                today,
                            )
                        except ProviderError as err:
                            raise UpdateFailed(str(err)) from err

                    if batch is not None:
                        self._records.extend(batch.records)
                        price_per_m3 = batch.price_per_m3

                        self._running_sum = statistics.async_push_records(
                            self.hass,
                            self._statistic_id,
                            self.entry.title,
                            batch.records,
                            self._running_sum,
                        )
                        if batch.records:
                            self._last_statistic_date = max(
                                r.record_date for r in batch.records
                            )
        finally:
            await provider.async_close()

        cutoff = today - timedelta(days=_HISTORY_WINDOW_DAYS)
        self._records = sorted(
            (r for r in self._records if r.record_date >= cutoff),
            key=lambda r: r.record_date,
        )

        return self._build_data(today, price_per_m3)

    def _build_data(self, today: date, price_per_m3: float) -> AquaWatchData:
        options = self.entry.options
        records = self._records

        last_sync = (
            datetime.combine(
                max(r.record_date for r in records),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            if records
            else datetime.now(timezone.utc)
        )
        data_stale = bool(records) and (
            today - records[-1].record_date
        ).days > _STALE_AFTER_DAYS

        forecast_volume = forecast_month_end_volume_m3(records, today)
        forecast_cost = forecast_month_end_cost(records, today, price_per_m3)

        month_records = [
            r
            for r in records
            if r.record_date.month == today.month and r.record_date.year == today.year
        ]
        cost_month_to_date = (
            (sum(r.liters for r in month_records) / 1000) * price_per_m3
            if month_records
            else None
        )

        household_size = options.get(OPT_HOUSEHOLD_SIZE, DEFAULT_HOUSEHOLD_SIZE)
        recent_30 = [r for r in records if (today - r.record_date).days <= 30]
        avg_liters_per_day = (
            sum(r.liters for r in recent_30) / len(recent_30) if recent_30 else 0.0
        )
        eco_score, eco_grade, eco_tip = compute_eco_score(
            avg_liters_per_day, household_size
        )

        vs_week = _percent_change(records, today, 7)
        vs_month = _percent_change(records, today, 30)
        vs_year = _percent_change(records, today, 365)

        leak_suspected = detect_sustained_leak(
            records,
            baseline_days=_BASELINE_DAYS,
            threshold_ratio=options.get(
                OPT_LEAK_THRESHOLD_RATIO, DEFAULT_LEAK_THRESHOLD_RATIO
            ),
            consecutive_days_required=options.get(
                OPT_LEAK_CONSECUTIVE_DAYS, DEFAULT_LEAK_CONSECUTIVE_DAYS
            ),
        )
        anomaly_detected = detect_statistical_anomaly(
            records,
            baseline_days=_BASELINE_DAYS,
            zscore_threshold=options.get(
                OPT_ANOMALY_ZSCORE_THRESHOLD, DEFAULT_ANOMALY_ZSCORE_THRESHOLD
            ),
        )

        budget_amount = options.get(OPT_BUDGET_AMOUNT, 0) or 0
        budget_unit = options.get(OPT_BUDGET_UNIT, BUDGET_UNIT_EUR)
        projected = forecast_cost if budget_unit == BUDGET_UNIT_EUR else forecast_volume
        budget_exceeded = bool(budget_amount) and (
            projected is not None and projected > budget_amount
        )

        if self.data:
            if leak_suspected and not self.data.leak_suspected:
                self.hass.bus.async_fire(
                    EVENT_LEAK_SUSPECTED, {"entry_id": self.entry.entry_id}
                )
            if anomaly_detected and not self.data.anomaly_detected:
                self.hass.bus.async_fire(
                    EVENT_ANOMALY_DETECTED, {"entry_id": self.entry.entry_id}
                )
            if budget_exceeded and not self.data.budget_exceeded:
                self.hass.bus.async_fire(
                    EVENT_BUDGET_EXCEEDED, {"entry_id": self.entry.entry_id}
                )

        return AquaWatchData(
            records=records,
            price_per_m3=price_per_m3,
            last_sync=last_sync,
            forecast_volume_m3=forecast_volume,
            forecast_cost=forecast_cost,
            eco_score=eco_score,
            eco_grade=eco_grade,
            eco_tip=eco_tip,
            vs_last_week_pct=vs_week,
            vs_last_month_pct=vs_month,
            vs_last_year_pct=vs_year,
            leak_suspected=leak_suspected,
            anomaly_detected=anomaly_detected,
            budget_exceeded=budget_exceeded,
            data_stale=data_stale,
            cost_month_to_date=cost_month_to_date,
        )

    async def async_recalibrate_baseline(self) -> None:
        """Drop all but the last _BASELINE_DAYS records to reset the leak/anomaly baseline."""
        self._records = sorted(self._records, key=lambda r: r.record_date)[
            -_BASELINE_DAYS:
        ]
        await self.async_request_refresh()

    def seed_from_backfill(
        self, records: list[ConsumptionRecord], running_sum: float
    ) -> None:
        """Seed in-memory records/running-sum from a prior historical backfill.

        Called once, before the first refresh, so the coordinator's own
        incremental fetch continues from where the backfill left off instead
        of re-fetching and re-pushing the same days into long-term statistics.
        """
        self._records = sorted(records, key=lambda r: r.record_date)
        self._running_sum = running_sum


def _percent_change(
    records: list[ConsumptionRecord], today: date, days_back: int
) -> float | None:
    """Compare the last `days_back` days to the equivalent prior period."""
    current_start = today - timedelta(days=days_back - 1)
    previous_start = current_start - timedelta(days=days_back)
    previous_end = current_start - timedelta(days=1)

    current = [r for r in records if current_start <= r.record_date <= today]
    previous = [
        r for r in records if previous_start <= r.record_date <= previous_end
    ]

    if not current or not previous:
        return None

    current_total = sum(r.liters for r in current)
    previous_total = sum(r.liters for r in previous)
    if previous_total == 0:
        return None

    return ((current_total - previous_total) / previous_total) * 100
