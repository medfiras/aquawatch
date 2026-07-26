"""Diagnostics support for AquaWatch."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CONTRACT_ID, CONF_EMAIL, CONF_PASSWORD, CONF_PROVIDER, DOMAIN
from .providers import get_provider_class

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}


async def _async_fetch_raw_consumption_response(
    entry: ConfigEntry,
) -> dict[str, Any] | str:
    """Best-effort fetch of the provider's raw consumption-history response.

    Temporary debugging aid to check whether the portal exposes a more
    detailed tariff breakdown than the single blended `prixMoyenEau` figure
    AquaWatch currently uses. Returns a short error string instead of
    raising if anything goes wrong, since diagnostics must not fail just
    because this best-effort call did.
    """
    provider_cls = get_provider_class(entry.data[CONF_PROVIDER])
    provider = provider_cls()
    try:
        if not hasattr(provider, "async_get_raw_daily_consumption"):
            return "not supported by this provider"
        await provider.async_authenticate(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
        end = date.today()
        start = end - timedelta(days=7)
        return await provider.async_get_raw_daily_consumption(
            entry.data[CONF_CONTRACT_ID], start, end
        )
    except Exception as err:  # noqa: BLE001 - best-effort debug aid, must not break diagnostics
        return f"error fetching raw consumption response: {err}"
    finally:
        await provider.async_close()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for an AquaWatch config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    return {
        "debug_raw_consumption_response": await _async_fetch_raw_consumption_response(
            entry
        ),
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
            "last_sync": data.last_sync.isoformat() if data else None,
            "forecast_volume_m3": data.forecast_volume_m3 if data else None,
            "forecast_cost": data.forecast_cost if data else None,
            "eco_score": data.eco_score if data else None,
            "eco_grade": data.eco_grade if data else None,
            "eco_tip": data.eco_tip if data else None,
            "vs_last_week_pct": data.vs_last_week_pct if data else None,
            "vs_last_month_pct": data.vs_last_month_pct if data else None,
            "vs_last_year_pct": data.vs_last_year_pct if data else None,
            "leak_suspected": data.leak_suspected if data else None,
            "anomaly_detected": data.anomaly_detected if data else None,
            "budget_exceeded": data.budget_exceeded if data else None,
            "data_stale": data.data_stale if data else None,
            "cost_month_to_date": data.cost_month_to_date if data else None,
        },
    }
