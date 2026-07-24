"""Water provider abstraction and registry for AquaWatch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ..models import ConsumptionBatch, ContractInfo


class WaterProvider(ABC):
    """Base interface every water utility provider must implement."""

    provider_id: str
    display_name: str
    available: bool = True

    @abstractmethod
    async def async_authenticate(self, email: str, password: str) -> None:
        """Authenticate with the provider using the given credentials."""

    @abstractmethod
    async def async_list_contracts(self) -> list[ContractInfo]:
        """Return the contracts/meters available on the authenticated account."""

    @abstractmethod
    async def async_get_daily_consumption(
        self, contract_id: str, start: date, end: date
    ) -> ConsumptionBatch:
        """Return daily consumption records for a contract between start and end."""

    @abstractmethod
    async def async_close(self) -> None:
        """Release any resources (HTTP session, etc.) held by the provider."""


_PROVIDER_REGISTRY: dict[str, type[WaterProvider]] = {}


def register_provider(provider_cls: type[WaterProvider]) -> type[WaterProvider]:
    """Class decorator that registers a provider implementation."""
    _PROVIDER_REGISTRY[provider_cls.provider_id] = provider_cls
    return provider_cls


def get_provider_class(provider_id: str) -> type[WaterProvider]:
    """Look up a registered provider class by its provider_id."""
    return _PROVIDER_REGISTRY[provider_id]


def list_provider_classes() -> list[type[WaterProvider]]:
    """Return all registered provider classes, available ones first."""
    return sorted(
        _PROVIDER_REGISTRY.values(),
        key=lambda cls: (not cls.available, cls.provider_id),
    )


# Importing the concrete provider modules registers them (via the
# @register_provider decorator) as a side effect. This must happen whenever
# the `providers` package itself is imported, so that any code path — config
# flow, coordinator setup on a plain HA restart, etc. — that imports this
# package gets a fully populated registry, regardless of whether it also
# happens to import config_flow.py. Placed at the bottom of the module (after
# WaterProvider, register_provider, get_provider_class, and
# list_provider_classes are defined) because the submodules do
# `from . import WaterProvider, register_provider`, which requires this
# module to already be fully defined by the time they're imported.
from . import saur, sedif, suez, veolia  # noqa: F401,E402
