"""Heating switch for Eberspächer EasyStart Web."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)

from . import EberspaecherConfigEntry
from .api import EberspaecherError
from .const import (
    MODE_HEATING,
    MODE_OFF,
    MODE_VENTILATION,
    REFRESH_DELAY_COMMAND,
    STATE_ON,
)
from .entity import EberspaecherEntity

SERVICE_START = "start_heating"


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

    def __init__(self, coordinator, imei: str, address: int) -> None:
        super().__init__(coordinator, imei, address, "heating")

    @property
    def is_on(self) -> bool:
        return self.heater.get("state") == STATE_ON

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
                self._imei, self._address, mode, runtime
            )
        except EberspaecherError as err:
            raise HomeAssistantError(f"Could not send the command: {err}") from err
        self.coordinator.async_schedule_refresh(REFRESH_DELAY_COMMAND)

    async def async_start_heating(
        self, runtime: int | None = None, mode: str = MODE_HEATING
    ) -> None:
        """Start the heater for a given time (defaults to the Run time number)."""
        if runtime is None:
            runtime = self.coordinator.runtimes.get(
                (self._imei, self._address),
                self.coordinator.runtime_limits(self._imei, self._address)[2],
            )
        await self._async_send(mode, runtime)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_start_heating()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send(MODE_OFF, None)
