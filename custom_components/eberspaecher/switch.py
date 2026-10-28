"""Heating switch for Eberspächer EasyStart Web."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.event import async_call_later

from . import EberspaecherConfigEntry
from .api import EberspaecherError
from .const import (
    MODE_HEATING,
    MODE_OFF,
    MODE_VENTILATION,
    REFRESH_DELAY_COMMAND,
    STATE_ON,
)
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity

SERVICE_START = "start_heating"
PENDING_TIMEOUT = 90  # seconds to show the requested state while waiting for the cloud


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one switch per heater and register the start service."""
    coordinator = entry.runtime_data
    async_add_entities(
        EberspaecherHeatingSwitch(coordinator, imei, address)
        for imei, device in coordinator.data.items()
        for address in device.heaters
    )

    async_get_current_platform().async_register_entity_service(
        SERVICE_START,
        {
            vol.Optional("runtime"): vol.All(vol.Coerce(int), vol.Range(min=1, max=600)),
            vol.Optional("mode", default=MODE_HEATING): vol.In(
                [MODE_HEATING, MODE_VENTILATION]
            ),
        },
        "async_start_heating",
    )


class EberspaecherHeatingSwitch(EberspaecherEntity, SwitchEntity):
    """Switch that starts (for the configured run time) or stops a heater."""

    _attr_name = "Heating"
    _attr_icon = "mdi:car-defrost-front"

    def __init__(
        self, coordinator: EberspaecherCoordinator, imei: str, address: int
    ) -> None:
        super().__init__(coordinator, imei, address, "heating")
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

    @property
    def is_on(self) -> bool:
        if self._pending is not None and time.monotonic() < self._pending_until:
            return self._pending
        return self.heater.get("state") == STATE_ON

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending is not None:
            confirmed = (self.heater.get("state") == STATE_ON) == self._pending
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
            raise HomeAssistantError(f"Could not send the command: {err}") from err
        self._pending = mode != MODE_OFF
        self._pending_until = time.monotonic() + PENDING_TIMEOUT
        self._clear_pending_timer()
        self._cancel_pending = async_call_later(
            self.hass, PENDING_TIMEOUT, self._pending_expired
        )
        self.async_write_ha_state()
        self.coordinator.async_schedule_refresh(REFRESH_DELAY_COMMAND)

    async def async_start_heating(
        self, runtime: int | None = None, mode: str = MODE_HEATING
    ) -> None:
        """Start the heater for a given time (defaults to the Run time number)."""
        low, high, default = self.coordinator.runtime_limits(
            self._imei, self._heater_address
        )
        if runtime is None:
            runtime = self.coordinator.runtimes.get(
                (self._imei, self._heater_address), default
            )
        elif not low <= runtime <= high:
            raise ServiceValidationError(
                f"Run time must be between {low} and {high} minutes for this heater"
            )
        if not self.coordinator.supports_mode(self._imei, self._heater_address, mode):
            raise ServiceValidationError(f"This heater does not support {mode}")
        await self._async_send(mode, runtime)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_start_heating()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send(MODE_OFF, None)
