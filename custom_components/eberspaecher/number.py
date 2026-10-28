"""Run time setting for Eberspächer EasyStart Web."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EberspaecherConfigEntry
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one run-time number per heater."""
    coordinator = entry.runtime_data
    async_add_entities(
        EberspaecherRunTime(coordinator, imei, address)
        for imei, device in coordinator.data.items()
        for address in device.heaters
    )


class EberspaecherRunTime(EberspaecherEntity, RestoreNumber):
    """How long the Heating switch runs the heater when turned on."""

    _attr_name = "Run time"
    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_step = 1

    def __init__(
        self, coordinator: EberspaecherCoordinator, imei: str, address: int
    ) -> None:
        super().__init__(coordinator, imei, address, "run_time")
        self._key = (imei, address)
        low, high, default = coordinator.runtime_limits(imei, address)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_value = default
        coordinator.runtimes[(imei, address)] = default

    async def async_added_to_hass(self) -> None:
        """Restore the last chosen run time after a restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = int(last.native_value)
            if self._attr_native_min_value <= value <= self._attr_native_max_value:
                self._attr_native_value = value
                self.coordinator.runtimes[self._key] = value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = int(value)
        self.coordinator.runtimes[self._key] = int(value)
        self.async_write_ha_state()
