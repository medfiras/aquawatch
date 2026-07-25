"""Services for AquaWatch."""

from __future__ import annotations

import csv
import io

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_EXPORT_CSV,
    SERVICE_FORCE_REFRESH,
    SERVICE_RECALIBRATE_BASELINE,
)

ATTR_ENTRY_ID = "entry_id"

_SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_ENTRY_ID): cv.string})

_services_registered = False


def async_setup_services(hass: HomeAssistant) -> None:
    """Register AquaWatch services once, regardless of how many entries exist."""
    global _services_registered
    if _services_registered:
        return
    _services_registered = True

    def _get_coordinator(call: ServiceCall):
        entry_id = call.data[ATTR_ENTRY_ID]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entry_id",
                translation_placeholders={"entry_id": entry_id},
            )
        return coordinator

    async def _handle_force_refresh(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call)
        await coordinator.async_request_refresh()

    async def _handle_recalibrate(call: ServiceCall) -> None:
        coordinator = _get_coordinator(call)
        await coordinator.async_recalibrate_baseline()

    async def _handle_export_csv(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(call)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["date", "liters", "cumulative_index_m3", "is_estimated"])
        for record in coordinator.data.records:
            writer.writerow(
                [
                    record.record_date.isoformat(),
                    record.liters,
                    record.cumulative_index_m3,
                    record.is_estimated,
                ]
            )
        return {"csv": buffer.getvalue()}

    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_REFRESH, _handle_force_refresh, schema=_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALIBRATE_BASELINE,
        _handle_recalibrate,
        schema=_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_CSV,
        _handle_export_csv,
        schema=_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
