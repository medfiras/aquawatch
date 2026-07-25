"""tests/test_coordinator.py"""

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.aquawatch.coordinator import (
    _HISTORY_WINDOW_DAYS,
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

    # get_last_statistics is only consulted once, to seed the running sum.
    assert mock_get_last_stats.call_count == 1


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
            "custom_components.aquawatch.coordinator.statistics.async_push_records",
            return_value=500.12,
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

    data = coordinator._build_data(today, price_per_m3=4.0)

    assert data.cost_month_to_date == pytest.approx((100.0 + 200.0) / 1000 * 4.0)


def test_build_data_cost_month_to_date_none_without_current_month_records(hass) -> None:
    coordinator = _build_coordinator(hass)
    today = date(2024, 3, 15)
    coordinator._records = [
        _record(date(2024, 2, 28), 500.0),
    ]

    data = coordinator._build_data(today, price_per_m3=4.0)

    assert data.cost_month_to_date is None


def test_seed_from_backfill_sets_records_sorted_and_running_sum(hass) -> None:
    coordinator = _build_coordinator(hass)
    records = [
        _record(date(2024, 3, 2), 150.0),
        _record(date(2024, 3, 1), 100.0),
    ]

    coordinator.seed_from_backfill(records, running_sum=42.5)

    assert coordinator._records == sorted(records, key=lambda r: r.record_date)
    assert coordinator._running_sum == 42.5


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
        coordinator.seed_from_backfill(backfilled_records, running_sum=0.22)
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
