"""Repairs for AquaWatch."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

from .const import DOMAIN


def async_create_scraping_broken_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Surface a non-fixable issue when the provider's page structure changed."""
    async_create_issue(
        hass,
        DOMAIN,
        f"scraping_broken_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="scraping_broken",
        learn_more_url="https://github.com/medfiras/aquawatch/issues",
    )
