"""tests/test_repairs.py"""

from unittest.mock import patch

from custom_components.aquawatch.const import DOMAIN
from custom_components.aquawatch.repairs import async_create_scraping_broken_issue


async def test_creates_non_fixable_issue(hass) -> None:
    with patch(
        "custom_components.aquawatch.repairs.async_create_issue"
    ) as mock_create:
        async_create_scraping_broken_issue(hass, "entry-1")

    mock_create.assert_called_once()
    args, kwargs = mock_create.call_args
    assert args[0] is hass
    assert args[1] == DOMAIN
    assert args[2] == "scraping_broken_entry-1"
    assert kwargs["is_fixable"] is False
