"""Diagnostics support for AquaWatch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for an AquaWatch config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "record_count": len(data.records) if data else 0,
        "last_records": [
            {
                "date": r.record_date.isoformat(),
                "liters": r.liters,
                "cumulative_index_m3": r.cumulative_index_m3,
                "is_estimated": r.is_estimated,
            }
            for r in (data.records[-14:] if data else [])
        ],
        "computed": {
            "price_per_m3": data.price_per_m3 if data else None,
            "eco_score": data.eco_score if data else None,
            "leak_suspected": data.leak_suspected if data else None,
            "anomaly_detected": data.anomaly_detected if data else None,
            "budget_exceeded": data.budget_exceeded if data else None,
            "data_stale": data.data_stale if data else None,
        },
    }
