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
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .api import (
    EberspaecherApi,
    EberspaecherAuthError,
    EberspaecherConnectionError,
)
from .const import (
    CONF_COLD_BELOW,
    CONF_COLD_RUNTIME,
    CONF_IDLE_INTERVAL,
    CONF_LEARN_ENABLED,
    CONF_LEARN_ENTITY,
    CONF_LEARN_MARGIN,
    CONF_LEARN_STATE,
    CONF_LEARN_TRACKER,
    CONF_MILD_RUNTIME,
    CONF_NO_HEAT_ABOVE,
    CONF_NOTIFY_SERVICE,
    CONF_READY_BY,
    CONF_REMINDER_DAYS,
    CONF_REMINDER_ENABLED,
    CONF_SCHEDULE_ENABLED,
    CONF_TEMPERATURE_ENTITY,
    CONF_VENT_ABOVE,
    CONF_VENT_ENABLED,
    CONF_VENT_RUNTIME,
    CONF_VERY_COLD_BELOW,
    CONF_VERY_COLD_RUNTIME,
    CONF_WEEKDAYS,
    CONF_WORK_ENABLED,
    CONF_WORK_READY_BY,
    CONF_WORK_TEMPERATURE_ENTITY,
    CONF_WORK_WEEKDAYS,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_LEARN_MARGIN,
    DEFAULT_LEARN_STATE,
    DOMAIN,
    IDLE_INTERVAL_CHOICES,
    LEG_WORK,
    WEEKDAYS,
)
from .learning import LearnConfig
from .reminder import ReminderConfig
from .schedule import ScheduleConfig, longest_runtime_fits

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


def _is_notify(service: Any) -> bool:
    """Empty, a bare notify name, or notify.<name>: never another kind of action."""
    service = str(service or "").strip()
    domain, _, name = service.partition(".")
    return not service or not name or domain == "notify"


def _temperature() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=-50,
            max=60,
            step=0.5,
            unit_of_measurement="°C",
            mode=NumberSelectorMode.BOX,
        )
    )


def _minutes() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=1,
            max=600,
            step=1,
            unit_of_measurement="min",
            mode=NumberSelectorMode.BOX,
        )
    )


class EberspaecherOptionsFlow(OptionsFlow):
    """Polling, the start schedule and the summer ventilation rule."""

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    def _config(self, **changes: Any) -> ScheduleConfig:
        """The stored schedule options with the not yet saved changes applied."""
        return ScheduleConfig.from_options({**self.config_entry.options, **changes})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "polling",
                "schedule",
                "work",
                "learning",
                "heating_bands",
                "ventilation",
                "reminder",
            ],
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """How often the status is checked while idle."""
        if user_input is not None:
            return self._save(**{CONF_IDLE_INTERVAL: int(user_input[CONF_IDLE_INTERVAL])})

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
        return self.async_show_form(step_id="polling", data_schema=schema)

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """When the car should be ready, and which temperature to go by."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_WEEKDAYS):
                errors["base"] = "no_weekdays"
            elif not longest_runtime_fits(self._config(**user_input)):
                errors["base"] = "runtime_too_long"
            elif not _is_notify(user_input.get(CONF_NOTIFY_SERVICE)):
                errors["base"] = "notify_not_notify"
            else:
                # A cleared optional field is missing from the input; clear it too.
                return self._save(
                    **{CONF_TEMPERATURE_ENTITY: None, CONF_NOTIFY_SERVICE: None, **user_input}
                )

        cfg = self._config(**(user_input or {}))
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCHEDULE_ENABLED, default=cfg.enabled
                ): BooleanSelector(),
                vol.Required(
                    CONF_READY_BY, default=cfg.ready_by.strftime("%H:%M:%S")
                ): TimeSelector(),
                vol.Required(CONF_WEEKDAYS, default=list(cfg.weekdays)): SelectSelector(
                    SelectSelectorConfig(
                        options=WEEKDAYS,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_WEEKDAYS,
                    )
                ),
                vol.Optional(
                    CONF_TEMPERATURE_ENTITY,
                    description={"suggested_value": cfg.temperature_entity},
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    description={"suggested_value": cfg.notify_service},
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="schedule", data_schema=schema, errors=errors)

    async def async_step_heating_bands(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run time for each temperature band."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (
                user_input[CONF_NO_HEAT_ABOVE]
                > user_input[CONF_COLD_BELOW]
                > user_input[CONF_VERY_COLD_BELOW]
            ):
                errors["base"] = "bands_order"
            elif not longest_runtime_fits(self._config(**user_input)):
                errors["base"] = "runtime_too_long"
            else:
                return self._save(
                    **{
                        k: (int(v) if k.endswith("runtime") else float(v))
                        for k, v in user_input.items()
                    }
                )

        cfg = self._config(**(user_input or {}))
        schema = vol.Schema(
            {
                vol.Required(CONF_NO_HEAT_ABOVE, default=cfg.no_heat_above): _temperature(),
                vol.Required(CONF_MILD_RUNTIME, default=cfg.mild_runtime): _minutes(),
                vol.Required(CONF_COLD_BELOW, default=cfg.cold_below): _temperature(),
                vol.Required(CONF_COLD_RUNTIME, default=cfg.cold_runtime): _minutes(),
                vol.Required(
                    CONF_VERY_COLD_BELOW, default=cfg.very_cold_below
                ): _temperature(),
                vol.Required(
                    CONF_VERY_COLD_RUNTIME, default=cfg.very_cold_runtime
                ): _minutes(),
            }
        )
        return self.async_show_form(
            step_id="heating_bands", data_schema=schema, errors=errors
        )

    async def async_step_ventilation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional summer rule: ventilate the car when it is hot."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cfg = self._config(**user_input)
            if user_input[CONF_VENT_ABOVE] <= cfg.no_heat_above:
                errors["base"] = "vent_below_heating"
            elif not longest_runtime_fits(cfg):
                errors["base"] = "runtime_too_long"
            else:
                return self._save(
                    **{
                        CONF_VENT_ENABLED: bool(user_input[CONF_VENT_ENABLED]),
                        CONF_VENT_ABOVE: float(user_input[CONF_VENT_ABOVE]),
                        CONF_VENT_RUNTIME: int(user_input[CONF_VENT_RUNTIME]),
                    }
                )

        cfg = self._config(**(user_input or {}))
        schema = vol.Schema(
            {
                vol.Required(CONF_VENT_ENABLED, default=cfg.vent_enabled): BooleanSelector(),
                vol.Required(CONF_VENT_ABOVE, default=cfg.vent_above): _temperature(),
                vol.Required(CONF_VENT_RUNTIME, default=cfg.vent_runtime): _minutes(),
            }
        )
        return self.async_show_form(
            step_id="ventilation", data_schema=schema, errors=errors
        )

    async def async_step_reminder(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remind to run the heater when it has been idle for a long time."""
        if user_input is not None:
            return self._save(
                **{
                    CONF_REMINDER_ENABLED: bool(user_input[CONF_REMINDER_ENABLED]),
                    CONF_REMINDER_DAYS: int(user_input[CONF_REMINDER_DAYS]),
                }
            )

        current = ReminderConfig.from_options(dict(self.config_entry.options))
        schema = vol.Schema(
            {
                vol.Required(CONF_REMINDER_ENABLED, default=current.enabled): BooleanSelector(),
                vol.Required(CONF_REMINDER_DAYS, default=current.days): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=365,
                        step=1,
                        unit_of_measurement="days",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="reminder", data_schema=schema)

    async def async_step_work(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Also warm the car before leaving work."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_WORK_WEEKDAYS):
                errors["base"] = "no_weekdays"
            elif user_input.get(CONF_WORK_ENABLED) and not self.config_entry.options.get(
                CONF_LEARN_TRACKER
            ):
                errors["base"] = "work_needs_tracker"
            elif not longest_runtime_fits(
                ScheduleConfig.from_options({**self.config_entry.options, **user_input}, LEG_WORK)
            ):
                errors["base"] = "runtime_too_long"
            else:
                return self._save(
                    **{CONF_WORK_TEMPERATURE_ENTITY: None, **user_input}
                )

        options = {**self.config_entry.options, **(user_input or {})}
        cfg = ScheduleConfig.from_options(options, LEG_WORK)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WORK_ENABLED, default=bool(options.get(CONF_WORK_ENABLED, False))
                ): BooleanSelector(),
                vol.Required(
                    CONF_WORK_READY_BY, default=cfg.ready_by.strftime("%H:%M:%S")
                ): TimeSelector(),
                vol.Required(CONF_WORK_WEEKDAYS, default=list(cfg.weekdays)): SelectSelector(
                    SelectSelectorConfig(
                        options=WEEKDAYS,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_WEEKDAYS,
                    )
                ),
                vol.Optional(
                    CONF_WORK_TEMPERATURE_ENTITY,
                    description={"suggested_value": options.get(CONF_WORK_TEMPERATURE_ENTITY)},
                ): EntitySelector(EntitySelectorConfig(domain=["sensor", "weather"])),
            }
        )
        return self.async_show_form(step_id="work", data_schema=schema, errors=errors)

    async def async_step_learning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Learn the ready-by times from when you get in the car."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_LEARN_ENABLED) and not (
                user_input.get(CONF_LEARN_ENTITY) and user_input.get(CONF_LEARN_TRACKER)
            ):
                errors["base"] = "learn_needs_signal"
            elif str(user_input.get(CONF_LEARN_ENTITY) or "").startswith(
                "binary_sensor."
            ) and str(user_input.get(CONF_LEARN_STATE, "")).casefold() not in ("on", "off"):
                errors["base"] = "learn_state_binary"
            elif not user_input.get(CONF_LEARN_TRACKER) and self.config_entry.options.get(
                CONF_WORK_ENABLED
            ):
                errors["base"] = "work_needs_tracker"
            else:
                return self._save(
                    **{
                        CONF_LEARN_ENTITY: None,
                        CONF_LEARN_TRACKER: None,
                        **user_input,
                        CONF_LEARN_MARGIN: int(
                            user_input.get(CONF_LEARN_MARGIN, DEFAULT_LEARN_MARGIN)
                        ),
                    }
                )

        current = LearnConfig.from_options({**self.config_entry.options, **(user_input or {})})
        schema = vol.Schema(
            {
                vol.Required(CONF_LEARN_ENABLED, default=current.enabled): BooleanSelector(),
                vol.Optional(
                    CONF_LEARN_ENTITY, description={"suggested_value": current.entity}
                ): EntitySelector(EntitySelectorConfig(domain=["sensor", "binary_sensor"])),
                vol.Required(
                    CONF_LEARN_STATE, default=current.state or DEFAULT_LEARN_STATE
                ): TextSelector(),
                vol.Optional(
                    CONF_LEARN_TRACKER, description={"suggested_value": current.tracker}
                ): EntitySelector(EntitySelectorConfig(domain=["device_tracker", "person"])),
                vol.Required(CONF_LEARN_MARGIN, default=current.margin): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=30,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="learning", data_schema=schema, errors=errors)
