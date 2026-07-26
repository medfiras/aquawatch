"""DataUpdateCoordinator for AquaWatch."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
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
from .statistics import cost_statistic_id_for_entry, statistic_id_for_entry

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
    account_balance: float | None
    contract_status: str | None
    site_address: str | None
    meter_serial_number: str | None
    cost_total: float | None


def _format_site_address(contrat: dict) -> str | None:
    """Format a contract's SITE_* fields into a single address string."""
    rue = contrat.get("SITE_Rue")
    cp = contrat.get("SITE_CP")
    commune = contrat.get("SITE_Commune")

    parts = []
    if rue:
        parts.append(rue)
    cp_commune = " ".join(p for p in (cp, commune) if p)
    if cp_commune:
        parts.append(cp_commune)

    return ", ".join(parts) if parts else None


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
        self._cost_statistic_id = cost_statistic_id_for_entry(entry.entry_id)
        self._running_sum: float | None = None
        # Total cost accumulated since AquaWatch started tracking, pushed to
        # its own external statistic (see statistics.async_push_cost_records).
        # Unlike `_running_sum`/`cumulative_index_m3`, there is no real-world
        # absolute value to reconcile this against -- "total cost AquaWatch
        # has tracked" is well-defined on its own, so it needs no anchoring
        # after a restart, just the same lazy get_last_statistics seed.
        self._cost_running_sum: float | None = None
        # The last date already durably covered by long-term statistics, as
        # recorded by the recorder component itself. Unlike `self._records`
        # (in-memory only), this reflects state that survives a HA restart,
        # so it is what protects against re-fetching/re-pushing already
        # counted days after every restart. `None` means "not yet determined
        # this coordinator lifetime" (lazily resolved on first update, same
        # as `self._running_sum`).
        self._last_statistic_date: date | None = None
        # True once `_records` has been rebuilt from long-term statistics
        # (see `_async_reconstruct_records_from_statistics`) but not yet
        # re-anchored to a real absolute meter index. The "sum" stored in
        # long-term statistics is a relative running total seeded from 0.0
        # at the first backfill (fine for the Energy dashboard), NOT the
        # same scale as the physical `cumulative_index_m3` SEDIF reports --
        # so reconstructed records carry that relative value until the next
        # real provider fetch supplies an anchor to re-base them on.
        self._records_need_index_anchor = False

    async def _async_update_data(self) -> AquaWatchData:
        provider = self._provider_cls()
        try:
            try:
                await provider.async_authenticate(
                    self.entry.data[CONF_EMAIL], self.entry.data[CONF_PASSWORD]
                )
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err

            # Best-effort refresh of contract metadata (balance, status,
            # site address, meter serial number) -- a single extra call,
            # independent of whether there's new consumption data to fetch
            # this cycle. Not part of the WaterProvider interface (SEDIF-
            # specific), so skipped entirely for providers that don't
            # support it. Falls back to the previous cycle's values if the
            # call fails, rather than blocking the whole update over a
            # secondary enrichment call.
            account_balance = self.data.account_balance if self.data else None
            contract_status = self.data.contract_status if self.data else None
            site_address = self.data.site_address if self.data else None
            meter_serial_number = (
                self.data.meter_serial_number if self.data else None
            )
            if hasattr(provider, "async_get_raw_contract_details"):
                # Best-effort in the fullest sense: ANY failure here (a
                # network hiccup, a malformed/unexpected response shape,
                # not just a recognized ProviderError) must fall back to
                # last-known values rather than propagate, since an
                # uncaught exception here would abort _async_update_data
                # entirely -- including the primary consumption fetch --
                # and mark every entity on this coordinator "unavailable".
                try:
                    raw_details = await provider.async_get_raw_contract_details(
                        self._contract_id
                    )
                    contrat = raw_details.get("contrat", {})
                    account_balance = raw_details.get("solde", account_balance)
                    contract_status = contrat.get("Statut", contract_status)
                    site_address = _format_site_address(contrat) or site_address
                    compte_info = raw_details.get("compteInfo", [])
                    if compte_info:
                        meter_serial_number = compte_info[0].get(
                            "NUM_COMPTEUR", meter_serial_number
                        )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "Could not refresh contract metadata "
                        "(balance/status/address) this cycle",
                        exc_info=True,
                    )

            today = datetime.now(timezone.utc).date()

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
                        if not self._records:
                            # `self._records` only lives in memory and is
                            # wiped on every HA restart, unlike the
                            # long-term statistics pushed by
                            # `statistics.async_push_records` (which
                            # survive). Without this, every restart would
                            # blank out every sensor that needs more than
                            # the latest day (yesterday's consumption,
                            # weekly/monthly/yearly comparisons, eco-score,
                            # leak/anomaly baselines) until enough new days
                            # accumulated again.
                            self._records = (
                                await self._async_reconstruct_records_from_statistics(
                                    today
                                )
                            )
                            if self._records:
                                self._records_need_index_anchor = True
                    else:
                        self._running_sum = 0.0

                    last_cost_stats = await self.hass.async_add_executor_job(
                        get_last_statistics,
                        self.hass,
                        1,
                        self._cost_statistic_id,
                        True,
                        {"sum"},
                    )
                    cost_stats_for_id = (
                        last_cost_stats.get(self._cost_statistic_id)
                        if last_cost_stats
                        else None
                    )
                    self._cost_running_sum = (
                        cost_stats_for_id[0].get("sum") or 0.0
                        if cost_stats_for_id
                        else 0.0
                    )

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
                        if self._records_need_index_anchor and batch.records:
                            # Re-base the reconstructed records' index onto
                            # the real physical scale now that a fresh
                            # provider fetch supplies one: the reconstructed
                            # value immediately before this batch should
                            # equal the batch's first real index minus that
                            # day's own consumption.
                            anchor = min(batch.records, key=lambda r: r.record_date)
                            last_reconstructed = max(
                                self._records, key=lambda r: r.record_date
                            )
                            offset = (
                                anchor.cumulative_index_m3 - anchor.liters / 1000
                            ) - last_reconstructed.cumulative_index_m3
                            self._records = [
                                replace(
                                    r,
                                    cumulative_index_m3=r.cumulative_index_m3
                                    + offset,
                                )
                                for r in self._records
                            ]
                            self._records_need_index_anchor = False

                        self._records.extend(batch.records)
                        price_per_m3 = batch.price_per_m3

                        self._running_sum = statistics.async_push_records(
                            self.hass,
                            self._statistic_id,
                            self.entry.title,
                            batch.records,
                            self._running_sum,
                        )
                        self._cost_running_sum = statistics.async_push_cost_records(
                            self.hass,
                            self._cost_statistic_id,
                            self.entry.title,
                            batch.records,
                            batch.price_per_m3,
                            self._cost_running_sum or 0.0,
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

        return self._build_data(
            today,
            price_per_m3,
            account_balance,
            contract_status,
            site_address,
            meter_serial_number,
        )

    async def _async_reconstruct_records_from_statistics(
        self, today: date
    ) -> list[ConsumptionRecord]:
        """Rebuild the in-memory record window from durable long-term statistics.

        Each daily push (see `statistics.async_push_records`) stores both
        `state` (that day's liters, in m3) and `sum` (the running cumulative
        index, in m3) at midnight UTC, so a "day"-period query reconstructs
        exact `ConsumptionRecord`s. The `is_estimated` flag isn't stored in
        long-term statistics; it only affects a display attribute, not any
        detection/forecast calculation, so it defaults to `False` here.
        """
        start_time = datetime.combine(
            today - timedelta(days=_HISTORY_WINDOW_DAYS),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        stats = await self.hass.async_add_executor_job(
            statistics_during_period,
            self.hass,
            start_time,
            None,
            {self._statistic_id},
            "day",
            None,
            {"state", "sum"},
        )
        records = []
        for row in stats.get(self._statistic_id, []):
            row_start = row.get("start")
            state = row.get("state")
            row_sum = row.get("sum")
            if row_start is None or state is None or row_sum is None:
                continue
            records.append(
                ConsumptionRecord(
                    record_date=datetime.fromtimestamp(
                        row_start, tz=timezone.utc
                    ).date(),
                    liters=state * 1000,
                    cumulative_index_m3=row_sum,
                    is_estimated=False,
                )
            )
        return sorted(records, key=lambda r: r.record_date)

    def _build_data(
        self,
        today: date,
        price_per_m3: float,
        account_balance: float | None,
        contract_status: str | None,
        site_address: str | None,
        meter_serial_number: str | None,
    ) -> AquaWatchData:
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
            account_balance=account_balance,
            contract_status=contract_status,
            site_address=site_address,
            meter_serial_number=meter_serial_number,
            cost_total=self._cost_running_sum,
        )

    async def async_recalibrate_baseline(self) -> None:
        """Drop all but the last _BASELINE_DAYS records to reset the leak/anomaly baseline."""
        self._records = sorted(self._records, key=lambda r: r.record_date)[
            -_BASELINE_DAYS:
        ]
        await self.async_request_refresh()

    def seed_from_backfill(
        self,
        records: list[ConsumptionRecord],
        running_sum: float,
        cost_running_sum: float,
    ) -> None:
        """Seed in-memory records/running-sum from a prior historical backfill.

        Called once, before the first refresh, so the coordinator's own
        incremental fetch continues from where the backfill left off instead
        of re-fetching and re-pushing the same days into long-term statistics.
        """
        self._records = sorted(records, key=lambda r: r.record_date)
        self._running_sum = running_sum
        self._cost_running_sum = cost_running_sum


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
