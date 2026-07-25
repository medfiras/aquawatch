"""tests/test_config_flow.py"""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquawatch.const import (
    CONF_CONTRACT_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    DOMAIN,
)
from custom_components.aquawatch.models import ConsumptionBatch, ContractInfo
from custom_components.aquawatch.providers.exceptions import AuthError


class _FakeProvider:
    provider_id = "sedif"
    display_name = "L'Eau d'Île-de-France (SEDIF)"

    def __init__(self, contracts=None, auth_error=False):
        self._contracts = contracts or [ContractInfo("CTR-1", "Contrat CTR-1")]
        self._auth_error = auth_error

    async def async_authenticate(self, email, password):
        if self._auth_error:
            raise AuthError("bad credentials")

    async def async_list_contracts(self):
        return self._contracts

    async def async_get_daily_consumption(self, contract_id, start, end):
        return ConsumptionBatch(records=[], price_per_m3=4.0)

    async def async_close(self):
        pass


async def test_user_step_shows_provider_form(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_single_contract_creates_entry_directly(hass) -> None:
    fake_cls = lambda: _FakeProvider()
    with patch(
        "custom_components.aquawatch.config_flow.get_provider_class",
        return_value=fake_cls,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PROVIDER: "sedif"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "user@example.com", "password": "pw"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTRACT_ID] == "CTR-1"
    assert result["data"][CONF_EMAIL] == "user@example.com"
    # Single contract: no opaque contract label suffix, nothing to
    # disambiguate from.
    assert result["title"] == "L'Eau d'Île-de-France (SEDIF)"


async def test_multiple_contracts_prompts_contract_step(hass) -> None:
    contracts = [ContractInfo("CTR-1", "Contrat 1"), ContractInfo("CTR-2", "Contrat 2")]
    fake_cls = lambda: _FakeProvider(contracts=contracts)
    with patch(
        "custom_components.aquawatch.config_flow.get_provider_class",
        return_value=fake_cls,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PROVIDER: "sedif"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "user@example.com", "password": "pw"}
        )
        assert result["step_id"] == "contract"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONTRACT_ID: "CTR-2"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTRACT_ID] == "CTR-2"
    # Multiple contracts: title must disambiguate which one this entry is.
    assert result["title"] == "L'Eau d'Île-de-France (SEDIF) — Contrat 2"


async def test_invalid_auth_shows_error(hass) -> None:
    fake_cls = lambda: _FakeProvider(auth_error=True)
    with patch(
        "custom_components.aquawatch.config_flow.get_provider_class",
        return_value=fake_cls,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PROVIDER: "sedif"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "user@example.com", "password": "wrong"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_flow_updates_thresholds(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "update_interval_hours": 12,
            "leak_threshold_ratio": 2.0,
            "leak_consecutive_days": 3,
            "anomaly_zscore_threshold": 3.0,
            "budget_amount": 50.0,
            "budget_unit": "eur",
            "household_size": 2,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["update_interval_hours"] == 12


async def test_reauth_updates_existing_entry(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_PROVIDER: "sedif",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "oldpw",
            CONF_CONTRACT_ID: "CTR-1",
        },
    )
    entry.add_to_hass(hass)

    fake_cls = lambda: _FakeProvider()
    with patch(
        "custom_components.aquawatch.config_flow.get_provider_class",
        return_value=fake_cls,
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "user@example.com", "password": "newpw"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "newpw"
