from datetime import date

import pytest

from custom_components.aquawatch.providers import get_provider_class
from custom_components.aquawatch.providers.exceptions import ProviderUnavailable
from custom_components.aquawatch.providers.saur import SaurProvider
from custom_components.aquawatch.providers.suez import SuezProvider
from custom_components.aquawatch.providers.veolia import VeoliaProvider


@pytest.mark.parametrize(
    "provider_cls", [VeoliaProvider, SuezProvider, SaurProvider]
)
async def test_stub_provider_raises_on_authenticate(provider_cls) -> None:
    provider = provider_cls()
    with pytest.raises(ProviderUnavailable):
        await provider.async_authenticate("user@example.com", "pw")


@pytest.mark.parametrize(
    "provider_cls", [VeoliaProvider, SuezProvider, SaurProvider]
)
async def test_stub_provider_marked_unavailable(provider_cls) -> None:
    assert provider_cls.available is False


def test_stub_providers_registered() -> None:
    assert get_provider_class("veolia") is VeoliaProvider
    assert get_provider_class("suez") is SuezProvider
    assert get_provider_class("saur") is SaurProvider


async def test_stub_provider_close_is_noop() -> None:
    provider = VeoliaProvider()
    await provider.async_close()


async def test_stub_provider_list_contracts_raises() -> None:
    provider = SuezProvider()
    with pytest.raises(ProviderUnavailable):
        await provider.async_list_contracts()


async def test_stub_provider_get_consumption_raises() -> None:
    provider = SaurProvider()
    with pytest.raises(ProviderUnavailable):
        await provider.async_get_daily_consumption("x", date(2024, 1, 1), date(2024, 1, 2))
