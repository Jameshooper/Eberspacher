"""Buttons for Eberspächer EasyStart Web."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EberspaecherConfigEntry
from .api import EberspaecherError
from .const import REFRESH_DELAY_TEMPERATURE, SETTLE_MAX_READS
from .entity import EberspaecherEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one 'request temperature' button per device."""
    coordinator = entry.runtime_data
    async_add_entities(
        EberspaecherRequestTemperature(coordinator, imei)
        for imei in coordinator.data
    )


class EberspaecherRequestTemperature(EberspaecherEntity, ButtonEntity):
    """Ask the device for a fresh temperature reading.

    This wakes the device, so it is a manual button rather than something that
    is polled automatically.
    """

    _attr_name = "Request temperature"
    _attr_icon = "mdi:thermometer-auto"

    def __init__(self, coordinator, imei: str) -> None:
        super().__init__(coordinator, imei, None, "request_temperature")

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.async_request_temperature(self._imei)
        except EberspaecherError as err:
            raise HomeAssistantError(f"Could not request the temperature: {err}") from err
        self.coordinator.temperature_reads = SETTLE_MAX_READS
        self.coordinator.async_schedule_refresh(REFRESH_DELAY_TEMPERATURE)
