"""tests/test_coordinator.py"""

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.aquawatch.coordinator import (
    _HISTORY_WINDOW_DAYS,
    _format_site_address,
    _percent_change,
    async_fetch_with_shrinking_window,
)
from custom_components.aquawatch.models import ConsumptionBatch, ConsumptionRecord
from custom_components.aquawatch.providers.exceptions import ScrapingError
from custom_components.aquawatch.statistics import statistic_id_for_entry


def _record(day: date, liters: float) -> ConsumptionRecord:
    return ConsumptionRecord(
        record_date=day, liters=liters, cumulative_index_m3=0.0, is_estimated=False
    )


def test_percent_change_computes_week_over_week_increase() -> None:
    today = date(2024, 3, 15)
    records = []
    # previous week: 7 days at 100L
    for i in range(7, 14):
        records.append(_record(today - timedelta(days=i), 100.0))
    # current week (last 7 days incl. today): 7 days at 150L
    for i in range(0, 7):
        records.append(_record(today - timedelta(days=i), 150.0))

    result = _percent_change(records, today, days_back=7)

    assert result == pytest.approx(50.0)


def test_percent_change_returns_none_without_previous_period() -> None:
    today = date(2024, 3, 15)
    records = [_record(today - timedelta(days=i), 150.0) for i in range(0, 7)]

    assert _percent_change(records, today, days_back=7) is None


def test_percent_change_returns_none_with_empty_records() -> None:
    assert _percent_change([], date(2024, 3, 15), days_back=7) is None


async def test_scraping_error_creates_repair_issue(hass) -> None:
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.providers.exceptions import ScrapingError

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(
        side_effect=ScrapingError("layout changed")
    )
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={},
        ),
        patch(
            "custom_components.aquawatch.coordinator.async_create_scraping_broken_issue"
        ) as mock_issue,
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        from homeassistant.helpers.update_coordinator import UpdateFailed

        try:
            await coordinator._async_update_data()
        except UpdateFailed:
            pass

    mock_issue.assert_called_once_with(hass, entry.entry_id)


async def test_update_pushes_new_batch_into_statistics_with_threaded_running_sum(
    hass,
) -> None:
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    batch1 = ConsumptionBatch(records=[_record(date(2024, 3, 15), 150.0)], price_per_m3=4.0)
    batch2 = ConsumptionBatch(records=[_record(date(2024, 3, 16), 200.0)], price_per_m3=4.0)

    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(side_effect=[batch1, batch2])
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={},
        ) as mock_get_last_stats,
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records",
            side_effect=[10.15, 10.35],
        ) as mock_push,
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records",
            side_effect=[40.6, 41.4],
        ),
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        await coordinator._async_update_data()
        await coordinator._async_update_data()

    assert mock_push.call_count == 2
    first_call_args = mock_push.call_args_list[0].args
    second_call_args = mock_push.call_args_list[1].args

    assert first_call_args[1] == statistic_id_for_entry(entry.entry_id)
    assert first_call_args[3] == batch1.records
    assert first_call_args[4] == 0.0

    assert second_call_args[3] == batch2.records
    assert second_call_args[4] == 10.15

    # get_last_statistics is only consulted once per lifetime for each of
    # the volume and cost statistics (2 calls total), to seed their running
    # sums -- not on every update cycle.
    assert mock_get_last_stats.call_count == 2


async def test_restart_resumes_from_last_statistic_date_not_full_history_window(
    hass,
) -> None:
    """Reproduces the restart double-push bug: a fresh coordinator (empty
    `_records`, `_running_sum` None -- as after a HA restart) must derive its
    fetch-start date from the durable last statistic's own date, not from
    `_records` (which is always empty right after a restart) falling back to
    the full `_HISTORY_WINDOW_DAYS` window and re-pushing already-counted
    days.
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    today = date.today()
    last_statistic_date = today - timedelta(days=5)
    last_statistic_start_ts = datetime.combine(
        last_statistic_date, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()

    fake_batch = ConsumptionBatch(
        records=[_record(today, 120.0)], price_per_m3=4.0
    )

    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=fake_batch)
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={
                statistic_id_for_entry(entry.entry_id): [
                    {"sum": 500.0, "start": last_statistic_start_ts}
                ]
            },
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics_during_period",
            return_value={},
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records",
            return_value=500.12,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records",
            return_value=2500.6,
        ),
    ):
        # Simulate a fresh coordinator after a HA restart: `_records` is
        # empty and `_running_sum` is None, exactly like a brand new
        # instance -- the bug was that this alone caused a re-fetch of the
        # full 740-day history window on every restart.
        coordinator = AquaWatchCoordinator(hass, entry)
        assert coordinator._records == []
        assert coordinator._running_sum is None

        await coordinator._async_update_data()

    fake_provider.async_get_daily_consumption.assert_called_once()
    call_args = fake_provider.async_get_daily_consumption.call_args.args
    # call_args: (contract_id, start, today)
    assert call_args[1] == last_statistic_date + timedelta(days=1)
    assert call_args[1] != today - timedelta(days=_HISTORY_WINDOW_DAYS)

    assert coordinator._last_statistic_date == today


async def test_restart_reconstructs_records_from_long_term_statistics(
    hass,
) -> None:
    """After a restart, `_records` starts empty even though the full history
    is durably available in long-term statistics (pushed by
    `statistics.async_push_records`, which survive restarts unlike
    `_records`). Sensors needing more than the latest day (yesterday's
    consumption, weekly/monthly/yearly comparisons) would otherwise stay
    Unknown after every restart until enough new days re-accumulated. The
    coordinator must rebuild `_records` from `statistics_during_period`
    instead of leaving it empty.
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    today = date.today()
    last_statistic_date = today - timedelta(days=1)
    last_statistic_start_ts = datetime.combine(
        last_statistic_date, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()

    def _ts(day: date) -> float:
        return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp()

    statistic_id = statistic_id_for_entry(entry.entry_id)
    historical_rows = {
        statistic_id: [
            {"start": _ts(today - timedelta(days=3)), "state": 0.1, "sum": 5.0},
            {"start": _ts(today - timedelta(days=2)), "state": 0.2, "sum": 5.2},
            {"start": _ts(last_statistic_date), "state": 0.15, "sum": 5.35},
        ]
    }

    real_anchor_record = ConsumptionRecord(
        record_date=today, liters=120.0, cumulative_index_m3=50.12, is_estimated=False
    )
    fake_batch = ConsumptionBatch(records=[real_anchor_record], price_per_m3=4.0)

    fake_provider = AsyncMock()
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=fake_batch)
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={
                statistic_id: [{"sum": 5.35, "start": last_statistic_start_ts}]
            },
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics_during_period",
            return_value=historical_rows,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records",
            return_value=5.47,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records",
            return_value=27.35,
        ),
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        assert coordinator._records == []

        await coordinator._async_update_data()

    reconstructed_dates = [r.record_date for r in coordinator._records]
    assert reconstructed_dates == [
        today - timedelta(days=3),
        today - timedelta(days=2),
        last_statistic_date,
        today,
    ]

    # The reconstructed records' index must be re-anchored to the real
    # physical scale the fresh fetch reports (50.12 - 0.12 = 50.0 the day
    # before), not left in long-term statistics' relative running-sum scale
    # (which starts from 0.0 at the first backfill and is unrelated to the
    # meter's actual reading).
    offset = 44.65  # (50.12 - 0.12) - 5.35
    reconstructed = coordinator._records[0]
    assert reconstructed.liters == pytest.approx(100.0)
    assert reconstructed.cumulative_index_m3 == pytest.approx(5.0 + offset)
    assert reconstructed.is_estimated is False

    last_reconstructed = coordinator._records[2]
    assert last_reconstructed.record_date == last_statistic_date
    assert last_reconstructed.cumulative_index_m3 == pytest.approx(5.35 + offset)

    real_record = coordinator._records[3]
    assert real_record.record_date == today
    assert real_record.cumulative_index_m3 == pytest.approx(50.12)


async def test_index_anchor_retried_next_cycle_when_no_real_data_yet(
    hass,
) -> None:
    """If the incremental fetch right after reconstruction returns nothing
    (SEDIF hasn't published today's reading yet), the reconstructed records'
    index stays unanchored for that cycle -- but the coordinator must retry
    the anchoring on the next cycle instead of giving up, once a real batch
    is finally available.
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch
    from custom_components.aquawatch.providers.exceptions import ScrapingError

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    today = date.today()
    last_statistic_date = today - timedelta(days=1)
    last_statistic_start_ts = datetime.combine(
        last_statistic_date, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()
    statistic_id = statistic_id_for_entry(entry.entry_id)
    historical_rows = {
        statistic_id: [
            {"start": last_statistic_start_ts, "state": 0.15, "sum": 5.35},
        ]
    }

    real_anchor_record = ConsumptionRecord(
        record_date=today, liters=120.0, cumulative_index_m3=50.12, is_estimated=False
    )
    fake_batch = ConsumptionBatch(records=[real_anchor_record], price_per_m3=4.0)

    fake_provider = AsyncMock()
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(
        side_effect=[ScrapingError("not published yet"), fake_batch]
    )
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={
                statistic_id: [{"sum": 5.35, "start": last_statistic_start_ts}]
            },
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics_during_period",
            return_value=historical_rows,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records",
            return_value=5.47,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records",
            return_value=27.35,
        ),
    ):
        coordinator = AquaWatchCoordinator(hass, entry)

        await coordinator._async_update_data()
        assert coordinator._records_need_index_anchor is True
        assert coordinator._records[0].cumulative_index_m3 == pytest.approx(5.35)

        await coordinator._async_update_data()

    assert coordinator._records_need_index_anchor is False
    offset = (50.12 - 0.12) - 5.35
    reconstructed = coordinator._records[0]
    assert reconstructed.record_date == last_statistic_date
    assert reconstructed.cumulative_index_m3 == pytest.approx(5.35 + offset)


def _build_coordinator(hass) -> "AquaWatchCoordinator":  # noqa: F821
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.aquawatch.coordinator.get_provider_class",
        return_value=lambda: None,
    ):
        return AquaWatchCoordinator(hass, entry)


def test_build_data_computes_cost_month_to_date(hass) -> None:
    coordinator = _build_coordinator(hass)
    today = date(2024, 3, 15)
    coordinator._records = [
        _record(date(2024, 3, 1), 100.0),
        _record(date(2024, 3, 10), 200.0),
        _record(date(2024, 2, 28), 500.0),  # previous month, must be excluded
    ]

    data = coordinator._build_data(
        today,
        price_per_m3=4.0,
        account_balance=None,
        contract_status=None,
        site_address=None,
        meter_serial_number=None,
    )

    assert data.cost_month_to_date == pytest.approx((100.0 + 200.0) / 1000 * 4.0)


def test_build_data_cost_month_to_date_none_without_current_month_records(hass) -> None:
    coordinator = _build_coordinator(hass)
    today = date(2024, 3, 15)
    coordinator._records = [
        _record(date(2024, 2, 28), 500.0),
    ]

    data = coordinator._build_data(
        today,
        price_per_m3=4.0,
        account_balance=None,
        contract_status=None,
        site_address=None,
        meter_serial_number=None,
    )

    assert data.cost_month_to_date is None


def test_seed_from_backfill_sets_records_sorted_and_running_sum(hass) -> None:
    coordinator = _build_coordinator(hass)
    records = [
        _record(date(2024, 3, 2), 150.0),
        _record(date(2024, 3, 1), 100.0),
    ]

    coordinator.seed_from_backfill(records, running_sum=42.5, cost_running_sum=200.0)

    assert coordinator._records == sorted(records, key=lambda r: r.record_date)
    assert coordinator._running_sum == 42.5
    assert coordinator._cost_running_sum == 200.0


async def test_seed_from_backfill_prevents_reduplicate_push_on_first_refresh(
    hass,
) -> None:
    """After a backfill seeds the coordinator up to today, first refresh must
    not re-fetch or re-push the same days into long-term statistics."""
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    today = date.today()
    backfilled_records = [
        _record(today - timedelta(days=1), 100.0),
        _record(today, 120.0),
    ]

    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock()
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records"
        ) as mock_push,
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        coordinator.seed_from_backfill(
            backfilled_records, running_sum=0.22, cost_running_sum=1.1
        )
        await coordinator._async_update_data()

    # The backfill already reached "today", so the coordinator's own
    # incremental window (last_known + 1 day .. today) is empty: it must not
    # query the provider again nor push anything further.
    fake_provider.async_get_daily_consumption.assert_not_called()
    mock_push.assert_not_called()
    assert coordinator._running_sum == pytest.approx(0.22)


async def test_fetch_with_shrinking_window_succeeds_on_later_attempt() -> None:
    from unittest.mock import AsyncMock

    today = date(2026, 7, 25)
    fake_batch = ConsumptionBatch(
        records=[_record(today, 100.0)], price_per_m3=4.0
    )
    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_get_daily_consumption = AsyncMock(
        side_effect=[
            ScrapingError("no data that far back"),
            ScrapingError("still no data"),
            fake_batch,
        ]
    )

    result = await async_fetch_with_shrinking_window(
        fake_provider, "CTR-1", today, attempts_days=(730, 365, 180)
    )

    assert result is fake_batch
    assert fake_provider.async_get_daily_consumption.call_count == 3
    calls = fake_provider.async_get_daily_consumption.call_args_list
    assert calls[0].args == ("CTR-1", today - timedelta(days=730), today)
    assert calls[1].args == ("CTR-1", today - timedelta(days=365), today)
    assert calls[2].args == ("CTR-1", today - timedelta(days=180), today)


async def test_fetch_with_shrinking_window_raises_last_error_if_all_fail() -> None:
    from unittest.mock import AsyncMock

    today = date(2026, 7, 25)
    last_error = ScrapingError("even 7 days back has nothing")
    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_get_daily_consumption = AsyncMock(
        side_effect=[ScrapingError("no data"), last_error]
    )

    with pytest.raises(ScrapingError, match="even 7 days back"):
        await async_fetch_with_shrinking_window(
            fake_provider, "CTR-1", today, attempts_days=(730, 7)
        )


async def test_incremental_scraping_error_does_not_raise_or_create_repair_issue(
    hass,
) -> None:
    """A ScrapingError on the steady-state incremental fetch (SEDIF hasn't
    published today's reading yet) must be treated as "nothing new this
    cycle", not as a portal-structure-broken repair issue -- unlike the
    same error during the cold-start fetch, which does create one (see
    test_scraping_error_creates_repair_issue).
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    today = date.today()
    last_statistic_date = today - timedelta(days=1)
    last_statistic_start_ts = datetime.combine(
        last_statistic_date, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()

    fake_provider = AsyncMock()
    # This test doesn't care about contract-metadata enrichment; a bare
    # AsyncMock() auto-creates ANY attribute (so hasattr() would wrongly
    # report support for async_get_raw_contract_details) -- delete it so
    # the coordinator correctly treats this fake as not supporting it,
    # same as a real provider that doesn't implement the method.
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(
        side_effect=ScrapingError("no reading published for this date yet")
    )
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={
                "aquawatch:" + entry.entry_id.lower() + "_consumption": [
                    {"sum": 5.0, "start": last_statistic_start_ts}
                ]
            },
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics_during_period",
            return_value={},
        ),
        patch(
            "custom_components.aquawatch.coordinator.async_create_scraping_broken_issue"
        ) as mock_issue,
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records"
        ) as mock_push,
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        # Must not raise UpdateFailed.
        data = await coordinator._async_update_data()

    fake_provider.async_get_daily_consumption.assert_called_once_with(
        "CTR-1", last_statistic_date + timedelta(days=1), today
    )
    mock_issue.assert_not_called()
    mock_push.assert_not_called()
    assert data is not None


async def test_update_enriches_data_with_contract_metadata_when_supported(hass) -> None:
    """When the provider supports it, balance/status/address/serial are
    fetched via async_get_raw_contract_details and parsed into AquaWatchData.
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    batch = ConsumptionBatch(records=[_record(date(2024, 3, 15), 150.0)], price_per_m3=4.0)
    raw_details = {
        "solde": -12.5,
        "contrat": {
            "Statut": "Actif",
            "SITE_Rue": "85 AV DE VERSAILLES",
            "SITE_CP": "93220",
            "SITE_Commune": "GAGNY",
        },
        "compteInfo": [{"NUM_COMPTEUR": "I26IA206176"}],
    }

    fake_provider = AsyncMock()
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=batch)
    fake_provider.async_get_raw_contract_details = AsyncMock(return_value=raw_details)
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={},
        ),
        patch("custom_components.aquawatch.coordinator.statistics.async_push_records"),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records"
        ),
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        data = await coordinator._async_update_data()

    fake_provider.async_get_raw_contract_details.assert_awaited_once_with("CTR-1")
    assert data.account_balance == -12.5
    assert data.contract_status == "Actif"
    assert data.site_address == "85 AV DE VERSAILLES, 93220 GAGNY"
    assert data.meter_serial_number == "I26IA206176"


async def test_update_keeps_previous_metadata_when_refresh_fails(hass) -> None:
    """A ScrapingError on the metadata enrichment call must not raise or
    wipe out previously known balance/status/address/serial values.
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator, AquaWatchData
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    batch = ConsumptionBatch(records=[_record(date(2024, 3, 15), 150.0)], price_per_m3=4.0)

    fake_provider = AsyncMock()
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=batch)
    fake_provider.async_get_raw_contract_details = AsyncMock(
        side_effect=ScrapingError("temporary glitch")
    )
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={},
        ),
        patch("custom_components.aquawatch.coordinator.statistics.async_push_records"),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records"
        ),
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        coordinator.data = AquaWatchData(
            records=[],
            price_per_m3=4.0,
            last_sync=datetime(2024, 3, 14, 6, 0),
            forecast_volume_m3=None,
            forecast_cost=None,
            eco_score=50,
            eco_grade="D",
            eco_tip="",
            vs_last_week_pct=None,
            vs_last_month_pct=None,
            vs_last_year_pct=None,
            leak_suspected=False,
            anomaly_detected=False,
            budget_exceeded=False,
            data_stale=False,
            cost_month_to_date=None,
            account_balance=-3.0,
            contract_status="Actif",
            site_address="85 AV DE VERSAILLES, 93220 GAGNY",
            meter_serial_number="I26IA206176",
            cost_total=None,
        )

        data = await coordinator._async_update_data()

    assert data.account_balance == -3.0
    assert data.contract_status == "Actif"
    assert data.site_address == "85 AV DE VERSAILLES, 93220 GAGNY"
    assert data.meter_serial_number == "I26IA206176"


def test_format_site_address_joins_all_parts() -> None:
    contrat = {
        "SITE_Rue": "85 AV DE VERSAILLES",
        "SITE_CP": "93220",
        "SITE_Commune": "GAGNY",
    }
    assert _format_site_address(contrat) == "85 AV DE VERSAILLES, 93220 GAGNY"


def test_format_site_address_handles_missing_parts() -> None:
    assert _format_site_address({"SITE_Rue": "85 AV DE VERSAILLES"}) == "85 AV DE VERSAILLES"
    assert _format_site_address({"SITE_CP": "93220", "SITE_Commune": "GAGNY"}) == "93220 GAGNY"
    assert _format_site_address({}) is None


async def test_update_survives_non_provider_error_during_metadata_refresh(hass) -> None:
    """Reproduces the real-world bug: a non-ProviderError exception (e.g. a
    raw network error or a malformed response) during the best-effort
    metadata refresh must NOT propagate and take down the whole update --
    that would mark every entity on this coordinator "unavailable", which is
    exactly what was observed against a real account.
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    batch = ConsumptionBatch(records=[_record(date.today(), 150.0)], price_per_m3=4.0)

    fake_provider = AsyncMock()
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=batch)
    # A raw, non-ProviderError exception -- e.g. a network timeout or a
    # malformed response causing an AttributeError/KeyError deep inside
    # the real provider. Before the fix, only ProviderError was caught
    # here, so this would propagate and fail the whole update.
    fake_provider.async_get_raw_contract_details = AsyncMock(
        side_effect=RuntimeError("connection reset by peer")
    )
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.get_last_statistics",
            return_value={},
        ),
        patch("custom_components.aquawatch.coordinator.statistics.async_push_records"),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records"
        ),
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        # Must not raise -- the primary consumption fetch must still succeed.
        data = await coordinator._async_update_data()

    assert data is not None
    assert data.records == batch.records
    assert data.account_balance is None


async def test_incremental_fetch_drops_records_the_provider_re_serves(hass) -> None:
    """Reproduces a real SEDIF bug: instead of ScrapingError or an empty
    result, some accounts get the last *published* day served again when
    asked for a day that isn't published yet. Accepting that blindly would
    re-push an already-covered day into long-term statistics every cycle,
    inflating the running sum on each poll (seen live: 14 identical
    same-day pushes from 14 hourly cycles, corrupting the Energy dashboard).
    """
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.aquawatch.const import (
        CONF_CONTRACT_ID,
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_PROVIDER,
        DOMAIN,
    )
    from custom_components.aquawatch.coordinator import AquaWatchCoordinator
    from custom_components.aquawatch.models import ConsumptionBatch

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    today = date.today()
    last_known = today - timedelta(days=1)
    stale_record = _record(last_known, 233.0)
    stale_batch = ConsumptionBatch(records=[stale_record], price_per_m3=4.0)

    fake_provider = AsyncMock()
    del fake_provider.async_get_raw_contract_details
    fake_provider.async_authenticate = AsyncMock()
    fake_provider.async_get_daily_consumption = AsyncMock(return_value=stale_batch)
    fake_provider.async_close = AsyncMock()

    with (
        patch(
            "custom_components.aquawatch.coordinator.get_provider_class",
            return_value=lambda: fake_provider,
        ),
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_records"
        ) as mock_push,
        patch(
            "custom_components.aquawatch.coordinator.statistics.async_push_cost_records"
        ) as mock_push_cost,
    ):
        coordinator = AquaWatchCoordinator(hass, entry)
        coordinator._records = [stale_record]
        coordinator._running_sum = 0.1
        coordinator._cost_running_sum = 0.5
        coordinator._last_statistic_date = last_known

        await coordinator._async_update_data()

    # No duplicate must have been appended, and nothing re-pushed.
    assert coordinator._records == [stale_record]
    assert coordinator._last_statistic_date == last_known
    mock_push.assert_called_once()
    assert mock_push.call_args.args[3] == []
    mock_push_cost.assert_called_once()
    assert mock_push_cost.call_args.args[3] == []
