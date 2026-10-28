"""Config flow for Eberspächer EasyStart Web."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    EberspaecherApi,
    EberspaecherAuthError,
    EberspaecherConnectionError,
)
from .const import (
    CONF_IDLE_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DOMAIN,
    IDLE_INTERVAL_CHOICES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class EberspaecherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup UI."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (polling interval)."""
        return EberspaecherOptionsFlow()

    async def _async_validate(self, email: str, password: str) -> str | None:
        """Return an error key, or None when the credentials work."""
        api = EberspaecherApi(async_get_clientsession(self.hass), email, password)
        try:
            await api.async_login()
            calls = await api.async_get_calls()
        except EberspaecherAuthError:
            return "invalid_auth"
        except EberspaecherConnectionError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating Eberspächer credentials")
            return "unknown"
        if not calls:
            return "no_devices"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: ask for the EasyStart Web login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            error = await self._async_validate(email, user_input[CONF_PASSWORD])
            if error is None:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when the stored login stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            error = await self._async_validate(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class EberspaecherOptionsFlow(OptionsFlow):
    """Let the user choose how often the status is checked while idle."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_IDLE_INTERVAL: int(user_input[CONF_IDLE_INTERVAL])}
            )

        current = self.config_entry.options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_IDLE_INTERVAL, default=str(current)): SelectSelector(
                    SelectSelectorConfig(
                        options=[str(m) for m in IDLE_INTERVAL_CHOICES],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_IDLE_INTERVAL,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
