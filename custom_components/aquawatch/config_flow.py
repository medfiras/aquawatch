"""Config flow for AquaWatch."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    BUDGET_UNIT_EUR,
    BUDGET_UNIT_M3,
    CONF_CONTRACT_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    DEFAULT_ANOMALY_ZSCORE_THRESHOLD,
    DEFAULT_HOUSEHOLD_SIZE,
    DEFAULT_LEAK_CONSECUTIVE_DAYS,
    DEFAULT_LEAK_THRESHOLD_RATIO,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MAX_UPDATE_INTERVAL_HOURS,
    MIN_UPDATE_INTERVAL_HOURS,
    OPT_ANOMALY_ZSCORE_THRESHOLD,
    OPT_BUDGET_AMOUNT,
    OPT_BUDGET_UNIT,
    OPT_HOUSEHOLD_SIZE,
    OPT_LEAK_CONSECUTIVE_DAYS,
    OPT_LEAK_THRESHOLD_RATIO,
    OPT_UPDATE_INTERVAL_HOURS,
)
from .providers import get_provider_class, list_provider_classes
from .providers.exceptions import AuthError, ProviderUnavailable


class AquaWatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AquaWatch."""

    VERSION = 1

    def __init__(self) -> None:
        self._provider_id: str | None = None
        self._provider_display_name: str | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._contracts: list[Any] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user pick a water provider."""
        available = [cls for cls in list_provider_classes() if cls.available]

        if user_input is not None:
            self._provider_id = user_input[CONF_PROVIDER]
            return await self.async_step_credentials()

        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER): vol.In(
                    {cls.provider_id: cls.display_name for cls in available}
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect and validate credentials against the chosen provider."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            provider_cls = get_provider_class(self._provider_id)
            provider = provider_cls()
            self._provider_display_name = provider.display_name
            try:
                await provider.async_authenticate(self._email, self._password)
                self._contracts = await provider.async_list_contracts()
            except AuthError:
                errors["base"] = "invalid_auth"
            except ProviderUnavailable:
                errors["base"] = "provider_unavailable"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            finally:
                await provider.async_close()

            if not errors:
                if not self._contracts:
                    errors["base"] = "no_contracts"
                elif len(self._contracts) == 1:
                    return await self._async_create_entry_for_contract(
                        self._contracts[0]
                    )
                else:
                    return await self.async_step_contract()

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="credentials", data_schema=schema, errors=errors
        )

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user choose which contract/meter this entry tracks."""
        if user_input is not None:
            chosen = next(
                c
                for c in self._contracts
                if c.contract_id == user_input[CONF_CONTRACT_ID]
            )
            return await self._async_create_entry_for_contract(chosen)

        schema = vol.Schema(
            {
                vol.Required(CONF_CONTRACT_ID): vol.In(
                    {c.contract_id: c.label for c in self._contracts}
                ),
            }
        )
        return self.async_show_form(step_id="contract", data_schema=schema)

    async def _async_create_entry_for_contract(
        self, contract: Any
    ) -> config_entries.ConfigFlowResult:
        unique_id = f"{self._provider_id}_{contract.contract_id}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # Only disambiguate with the contract label when the account
        # actually has more than one contract -- for the common single
        # contract case, appending an opaque label adds noise with nothing
        # to distinguish it from.
        title = (
            f"{self._provider_display_name} — {contract.label}"
            if len(self._contracts) > 1
            else self._provider_display_name
        )

        return self.async_create_entry(
            title=title,
            data={
                CONF_PROVIDER: self._provider_id,
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_CONTRACT_ID: contract.contract_id,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle re-authentication triggered by ConfigEntryAuthFailed."""
        self._provider_id = entry_data[CONF_PROVIDER]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for new credentials and update the existing entry in place."""
        errors: dict[str, str] = {}

        if user_input is not None:
            provider_cls = get_provider_class(self._provider_id)
            provider = provider_cls()
            try:
                await provider.async_authenticate(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            finally:
                await provider.async_close()

            if not errors:
                reauth_entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AquaWatchOptionsFlow:
        """Return the options flow handler for this config entry."""
        return AquaWatchOptionsFlow(config_entry)


class AquaWatchOptionsFlow(config_entries.OptionsFlow):
    """Handle AquaWatch options (thresholds, refresh interval, budget)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show and process the single options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_UPDATE_INTERVAL_HOURS,
                    default=options.get(
                        OPT_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_UPDATE_INTERVAL_HOURS, max=MAX_UPDATE_INTERVAL_HOURS
                    ),
                ),
                vol.Required(
                    OPT_LEAK_THRESHOLD_RATIO,
                    default=options.get(
                        OPT_LEAK_THRESHOLD_RATIO, DEFAULT_LEAK_THRESHOLD_RATIO
                    ),
                ): vol.Coerce(float),
                vol.Required(
                    OPT_LEAK_CONSECUTIVE_DAYS,
                    default=options.get(
                        OPT_LEAK_CONSECUTIVE_DAYS, DEFAULT_LEAK_CONSECUTIVE_DAYS
                    ),
                ): vol.Coerce(int),
                vol.Required(
                    OPT_ANOMALY_ZSCORE_THRESHOLD,
                    default=options.get(
                        OPT_ANOMALY_ZSCORE_THRESHOLD,
                        DEFAULT_ANOMALY_ZSCORE_THRESHOLD,
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    OPT_BUDGET_AMOUNT,
                    default=options.get(OPT_BUDGET_AMOUNT, 0),
                ): vol.Coerce(float),
                vol.Required(
                    OPT_BUDGET_UNIT,
                    default=options.get(OPT_BUDGET_UNIT, BUDGET_UNIT_EUR),
                ): vol.In([BUDGET_UNIT_EUR, BUDGET_UNIT_M3]),
                vol.Required(
                    OPT_HOUSEHOLD_SIZE,
                    default=options.get(OPT_HOUSEHOLD_SIZE, DEFAULT_HOUSEHOLD_SIZE),
                ): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
