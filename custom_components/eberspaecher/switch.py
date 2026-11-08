"""Heating switch for Eberspächer EasyStart Web."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import EberspaecherConfigEntry
from .api import EberspaecherError
from .const import (
    CONF_ALT_ENABLED,
    CONF_ALT_READY_BY,
    CONF_LEARN_ENABLED,
    CONF_LEARN_ENTITY,
    CONF_LEARN_TRACKER,
    CONF_SCHEDULE_ENABLED,
    DOMAIN,
    MODE_HEATING,
    MODE_OFF,
    MODE_VENTILATION,
    REFRESH_DELAY_COMMAND,
    STATE_ON,
)
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity
from .schedule import ScheduleConfig, longest_runtime_fits, start_times

SERVICE_START = "start_heating"
PENDING_TIMEOUT = 90  # seconds to show the requested state while waiting for the cloud


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one switch per heater and register the start service."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    for imei, device in coordinator.data.items():
        for address in device.heaters:
            entities.append(EberspaecherHeatingSwitch(coordinator, imei, address))
            # Only offer ventilation when the heater says it can do it.
            if coordinator.lists_mode(imei, address, MODE_VENTILATION):
                entities.append(EberspaecherVentilationSwitch(coordinator, imei, address))
    # The schedule is one per account, so its switch goes on the first device only.
    first = coordinator.primary_imei
    if first is not None:
        entities.append(EberspaecherScheduleSwitch(coordinator, entry, first))
        entities.append(EberspaecherLearnSwitch(coordinator, entry, first))
        entities.append(EberspaecherAltDaysSwitch(coordinator, entry, first))
    async_add_entities(entities)

    async_get_current_platform().async_register_entity_service(
        SERVICE_START,
        {
            vol.Optional("runtime"): vol.All(
                _not_bool, vol.Coerce(int), vol.Range(min=1, max=600)
            ),
            vol.Optional("mode"): vol.In([MODE_HEATING, MODE_VENTILATION]),
        },
        "async_start_heating",
    )


def _not_bool(value: Any) -> Any:
    """A run time is a number of minutes, not true or false (which would read as 1 or 0)."""
    if isinstance(value, bool):
        raise vol.Invalid("runtime must be a number of minutes")
    return value


class EberspaecherRunSwitch(EberspaecherEntity, SwitchEntity):
    """Switch that starts a heater in one mode (for the Run time) or stops it."""

    _mode = MODE_HEATING

    def __init__(
        self, coordinator: EberspaecherCoordinator, imei: str, address: int, key: str
    ) -> None:
        super().__init__(coordinator, imei, address, key)
        self._heater_address = address
        # The cloud takes a while to report a new state, so after a command the
        # switch shows the requested state until the cloud confirms it (or a timeout),
        # instead of flipping back to the old state and then forward again.
        self._pending: bool | None = None
        self._pending_until = 0.0
        self._cancel_pending: CALLBACK_TYPE | None = None

    async def async_will_remove_from_hass(self) -> None:
        self._clear_pending_timer()
        await super().async_will_remove_from_hass()

    def _clear_pending_timer(self) -> None:
        if self._cancel_pending:
            self._cancel_pending()
            self._cancel_pending = None

    @callback
    def _pending_expired(self, _now: Any) -> None:
        """The cloud never confirmed: show its real state and re-read it."""
        self._cancel_pending = None
        if self._pending is not None:
            self._pending = None
            self.async_write_ha_state()
            self.hass.async_create_task(self.coordinator.async_request_refresh())

    def _cloud_on(self) -> bool:
        """Whether the cloud reports this heater running in this switch's mode."""
        if self.heater.get("state") != STATE_ON:
            return False
        operation = self.heater.get("currentOperation") or {}
        settings = operation.get("operationSettings") or {}
        running = settings.get("operationMode")
        # Heating is the default when the cloud does not name the mode.
        return running == self._mode or (running is None and self._mode == MODE_HEATING)

    @property
    def is_on(self) -> bool:
        if self._pending is not None and time.monotonic() < self._pending_until:
            return self._pending
        return self._cloud_on()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending is not None:
            confirmed = self._cloud_on() == self._pending
            if confirmed or time.monotonic() >= self._pending_until:
                self._pending = None
                self._clear_pending_timer()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        heater = self.heater
        operation = heater.get("currentOperation") or {}
        settings = operation.get("operationSettings") or {}
        return {
            "heater_state": heater.get("heaterState"),
            "operation_mode": settings.get("operationMode"),
            "errors": heater.get("heaterErrors") or [],
        }

    async def _async_send(self, mode: str, runtime: int | None) -> None:
        try:
            await self.coordinator.api.async_set_heater(
                self._imei, self._heater_address, mode, runtime
            )
        except EberspaecherError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if mode == MODE_HEATING:
            self.coordinator.reminder.mark_heated()
        self._pending = mode == self._mode if mode != MODE_OFF else False
        self._pending_until = time.monotonic() + PENDING_TIMEOUT
        self._clear_pending_timer()
        self._cancel_pending = async_call_later(
            self.hass, PENDING_TIMEOUT, self._pending_expired
        )
        self.async_write_ha_state()
        self.coordinator.settle_reads = 0  # a new command gets fresh follow-up reads
        self.coordinator.async_schedule_refresh(REFRESH_DELAY_COMMAND)

    async def async_start_heating(
        self, runtime: int | None = None, mode: str | None = None
    ) -> None:
        """Start the heater for a given time (defaults to the Run time number)."""
        mode = mode or self._mode
        low, high, default = self.coordinator.runtime_limits(
            self._imei, self._heater_address, mode
        )
        if runtime is None:
            # The shared Run time number may sit outside this mode's limits.
            runtime = self.coordinator.runtimes.get(
                (self._imei, self._heater_address), default
            )
            runtime = min(max(runtime, low), high)
        elif not low <= runtime <= high:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="runtime_range",
                translation_placeholders={"low": str(low), "high": str(high)},
            )
        if not self.coordinator.supports_mode(self._imei, self._heater_address, mode):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_unsupported",
                translation_placeholders={"mode": mode},
            )
        await self._async_send(mode, runtime)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_start_heating()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send(MODE_OFF, None)


class EberspaecherHeatingSwitch(EberspaecherRunSwitch):
    """Heating switch."""

    _attr_translation_key = "heating"
    _attr_icon = "mdi:car-defrost-front"
    _mode = MODE_HEATING

    def __init__(
        self, coordinator: EberspaecherCoordinator, imei: str, address: int
    ) -> None:
        super().__init__(coordinator, imei, address, "heating")


class EberspaecherVentilationSwitch(EberspaecherRunSwitch):
    """Ventilation switch: runs the heater's fan without heating (summer)."""

    _attr_translation_key = "ventilation"
    _attr_icon = "mdi:fan"
    _mode = MODE_VENTILATION

    def __init__(
        self, coordinator: EberspaecherCoordinator, imei: str, address: int
    ) -> None:
        super().__init__(coordinator, imei, address, "ventilation")


class EberspaecherScheduleSwitch(EberspaecherEntity, SwitchEntity):
    """Turns the temperature schedule on or off (kept in the integration options)."""

    _attr_translation_key = "schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self, coordinator: EberspaecherCoordinator, entry: ConfigEntry, imei: str
    ) -> None:
        super().__init__(coordinator, imei, None, "schedule")
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for scheduler in (self.coordinator.scheduler, self.coordinator.work_scheduler):
            self.async_on_remove(scheduler.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self.coordinator.scheduler.config.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Today's times, which are the learned ones when there are any.
        today = dt_util.now().date()
        scheduler = self.coordinator.scheduler
        cfg = scheduler.config_for(today)
        nxt = scheduler.next_plan()
        work = self.coordinator.work_scheduler
        work_cfg = work.config_for(today)
        return {
            "ready_by": cfg.ready_by.strftime("%H:%M"),
            "weekdays": list(cfg.weekdays),
            "start_times": [t.strftime("%H:%M") for t in start_times(cfg)],
            "next_day": nxt[0].isoformat() if nxt else None,
            "next_ready_by": nxt[1].strftime("%H:%M") if nxt else None,
            "next_learned": nxt[2] if nxt else None,
            "last_result": scheduler.last_result,
            "work_ready_by": work_cfg.ready_by.strftime("%H:%M") if work_cfg.enabled else None,
            "work_weekdays": list(work_cfg.weekdays) if work_cfg.enabled else None,
            "work_start_times": (
                [t.strftime("%H:%M") for t in start_times(work_cfg)]
                if work_cfg.enabled
                else None
            ),
            "work_last_result": work.last_result,
        }

    async def _async_set(self, enabled: bool) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_SCHEDULE_ENABLED: enabled}
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class EberspaecherLearnSwitch(EberspaecherEntity, SwitchEntity):
    """Turns learning the ready-by times on or off (kept in the integration options)."""

    _attr_translation_key = "learn"
    _attr_icon = "mdi:school-outline"

    def __init__(
        self, coordinator: EberspaecherCoordinator, entry: ConfigEntry, imei: str
    ) -> None:
        super().__init__(coordinator, imei, None, "learn")
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.learner.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.learner.config.enabled

    async def _async_set(self, enabled: bool) -> None:
        options = self._entry.options
        if enabled and not (options.get(CONF_LEARN_ENTITY) and options.get(CONF_LEARN_TRACKER)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="learn_needs_signal"
            )
        self.hass.config_entries.async_update_entry(
            self._entry, options={**options, CONF_LEARN_ENABLED: enabled}
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class EberspaecherAltDaysSwitch(EberspaecherEntity, SwitchEntity):
    """Turns the second group of days (for example the weekend) on or off.

    Off keeps the time that was set, so turning it on again brings it back.
    """

    _attr_translation_key = "alt_enabled"
    _attr_icon = "mdi:calendar-weekend"

    def __init__(
        self, coordinator: EberspaecherCoordinator, entry: ConfigEntry, imei: str
    ) -> None:
        super().__init__(coordinator, imei, None, "alt_enabled")
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.scheduler.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.scheduler.config.alt_ready_by is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        options = {**self._entry.options, CONF_ALT_ENABLED: True}
        if not options.get(CONF_ALT_READY_BY):
            # No time kept yet: start from the first group's time.
            ready = self.coordinator.scheduler.config.ready_by
            options[CONF_ALT_READY_BY] = ready.strftime("%H:%M:%S")
        if not longest_runtime_fits(ScheduleConfig.from_options(options)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="runtime_too_long"
            )
        self.hass.config_entries.async_update_entry(self._entry, options=options)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_ALT_ENABLED: False}
        )
