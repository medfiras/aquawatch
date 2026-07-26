"""tests/test_statistics.py"""

from datetime import date
from unittest.mock import patch

import pytest

from custom_components.aquawatch.models import ConsumptionRecord
from custom_components.aquawatch.statistics import (
    async_push_cost_records,
    async_push_records,
    cost_statistic_id_for_entry,
    statistic_id_for_entry,
)


def test_statistic_id_for_entry_format() -> None:
    assert statistic_id_for_entry("abc123") == "aquawatch:abc123_consumption"


def test_statistic_id_for_entry_lowercases_ulid_entry_id() -> None:
    # HA config entry IDs are ULIDs (uppercase Crockford Base32), e.g.
    # "01ARZ3NDEKTSV4RRFFQ69G5FAV" -- HA's valid_statistic_id() only
    # accepts lowercase [0-9a-z_], so this must be lowercased.
    result = statistic_id_for_entry("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert result == "aquawatch:01arz3ndektsv4rrffq69g5fav_consumption"
    assert result == result.lower()


def test_async_push_records_returns_updated_running_sum(hass) -> None:
    records = [
        ConsumptionRecord(date(2024, 3, 15), 150.0, 100.0, False),
        ConsumptionRecord(date(2024, 3, 16), 200.0, 100.2, False),
    ]
    with patch(
        "custom_components.aquawatch.statistics.async_add_external_statistics"
    ) as mock_add:
        result = async_push_records(
            hass, "aquawatch:entry1_consumption", "Test Entry", records, running_sum_start=10.0
        )

    assert result == 10.0 + 0.15 + 0.2
    mock_add.assert_called_once()
    metadata, statistics = mock_add.call_args[0][1], mock_add.call_args[0][2]
    assert metadata["statistic_id"] == "aquawatch:entry1_consumption"
    assert len(statistics) == 2
    assert statistics[0]["sum"] == 10.15
    assert statistics[1]["sum"] == 10.35


def test_async_push_records_empty_list_returns_unchanged_sum(hass) -> None:
    with patch(
        "custom_components.aquawatch.statistics.async_add_external_statistics"
    ) as mock_add:
        result = async_push_records(
            hass, "aquawatch:entry1_consumption", "Test Entry", [], running_sum_start=5.0
        )
    assert result == 5.0
    mock_add.assert_not_called()


def test_cost_statistic_id_for_entry_format() -> None:
    assert cost_statistic_id_for_entry("abc123") == "aquawatch:abc123_cost"


def test_cost_statistic_id_for_entry_lowercases_ulid_entry_id() -> None:
    result = cost_statistic_id_for_entry("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert result == "aquawatch:01arz3ndektsv4rrffq69g5fav_cost"


def test_async_push_cost_records_returns_updated_running_sum(hass) -> None:
    records = [
        ConsumptionRecord(date(2024, 3, 15), 150.0, 100.0, False),
        ConsumptionRecord(date(2024, 3, 16), 200.0, 100.2, False),
    ]
    with patch(
        "custom_components.aquawatch.statistics.async_add_external_statistics"
    ) as mock_add:
        result = async_push_cost_records(
            hass,
            "aquawatch:entry1_cost",
            "Test Entry",
            records,
            price_per_m3=4.0,
            running_sum_start=10.0,
        )

    # 0.15 m3 * 4.0 + 0.2 m3 * 4.0 = 0.6 + 0.8
    assert result == pytest.approx(10.0 + 0.6 + 0.8)
    mock_add.assert_called_once()
    metadata, statistics = mock_add.call_args[0][1], mock_add.call_args[0][2]
    assert metadata["statistic_id"] == "aquawatch:entry1_cost"
    assert metadata["unit_of_measurement"] == "EUR"
    assert len(statistics) == 2
    assert statistics[0]["sum"] == pytest.approx(10.6)
    assert statistics[1]["sum"] == pytest.approx(11.4)


def test_async_push_cost_records_empty_list_returns_unchanged_sum(hass) -> None:
    with patch(
        "custom_components.aquawatch.statistics.async_add_external_statistics"
    ) as mock_add:
        result = async_push_cost_records(
            hass,
            "aquawatch:entry1_cost",
            "Test Entry",
            [],
            price_per_m3=4.0,
            running_sum_start=5.0,
        )
    assert result == 5.0
    mock_add.assert_not_called()
