from custom_components.aquawatch.providers import (
    WaterProvider,
    get_provider_class,
    list_provider_classes,
    register_provider,
)


def test_register_and_lookup_provider() -> None:
    @register_provider
    class _FakeProvider(WaterProvider):
        provider_id = "fake"
        display_name = "Fake"
        available = True

        async def async_authenticate(self, email, password):
            return None

        async def async_list_contracts(self):
            return []

        async def async_get_daily_consumption(self, contract_id, start, end):
            return None

        async def async_close(self):
            return None

    assert get_provider_class("fake") is _FakeProvider


def test_list_provider_classes_sorts_available_first() -> None:
    classes = list_provider_classes()
    available_flags = [cls.available for cls in classes]
    assert available_flags == sorted(available_flags, key=lambda a: not a)
