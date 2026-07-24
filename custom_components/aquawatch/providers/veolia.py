"""Veolia water provider stub for AquaWatch (not yet implemented)."""

from __future__ import annotations

from datetime import date

from ..models import ConsumptionBatch, ContractInfo
from . import WaterProvider, register_provider
from .exceptions import ProviderUnavailable

_NOT_IMPLEMENTED_MSG = (
    "Veolia n'est pas encore pris en charge. "
    "Contribuez sur https://github.com/fhajjej/aquawatch pour l'ajouter."
)


@register_provider
class VeoliaProvider(WaterProvider):
    """Placeholder for a future Veolia integration."""

    provider_id = "veolia"
    display_name = "Veolia"
    available = False

    async def async_authenticate(self, email: str, password: str) -> None:
        raise ProviderUnavailable(_NOT_IMPLEMENTED_MSG)

    async def async_list_contracts(self) -> list[ContractInfo]:
        raise ProviderUnavailable(_NOT_IMPLEMENTED_MSG)

    async def async_get_daily_consumption(
        self, contract_id: str, start: date, end: date
    ) -> ConsumptionBatch:
        raise ProviderUnavailable(_NOT_IMPLEMENTED_MSG)

    async def async_close(self) -> None:
        return None
