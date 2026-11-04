"""Run time setting for Eberspächer EasyStart Web."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from dataclasses import dataclass

from homeassistant.const import UnitOfTemperature

from . import EberspaecherConfigEntry
from .const import (
    CONF_COLD_BELOW,
    CONF_COLD_RUNTIME,
    CONF_MILD_RUNTIME,
    CONF_NO_HEAT_ABOVE,
    CONF_VENT_ABOVE,
    CONF_VENT_RUNTIME,
    CONF_VERY_COLD_BELOW,
    CONF_VERY_COLD_RUNTIME,
    MODE_VENTILATION,
)
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity, EberspaecherScheduleEntity
from .schedule import async_set_schedule_options


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one run-time number per heater."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        EberspaecherRunTime(coordinator, imei, address)
        for imei, device in coordinator.data.items()
        for address in device.heaters
    ]
    # The schedule is one per account, so its settings go on the first device only.
    imei = next(iter(coordinator.data), None)
    if imei is not None:
        has_ventilation = any(
            coordinator.lists_mode(imei, address, MODE_VENTILATION)
            for address in coordinator.data[imei].heaters
        )
        entities.extend(
            EberspaecherScheduleNumber(coordinator, entry, imei, spec)
            for spec in SCHEDULE_NUMBERS
            if has_ventilation or not spec.ventilation
        )
    async_add_entities(entities)


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


@dataclass(frozen=True)
class ScheduleNumberSpec:
    """One numeric schedule setting."""

    key: str
    option: str
    name: str
    temperature: bool
    ventilation: bool = False


SCHEDULE_NUMBERS = (
    ScheduleNumberSpec("no_heat_above", CONF_NO_HEAT_ABOVE, "No heating above", True),
    ScheduleNumberSpec("mild_runtime", CONF_MILD_RUNTIME, "Mild run time", False),
    ScheduleNumberSpec("cold_below", CONF_COLD_BELOW, "Cold at or below", True),
    ScheduleNumberSpec("cold_runtime", CONF_COLD_RUNTIME, "Cold run time", False),
    ScheduleNumberSpec("very_cold_below", CONF_VERY_COLD_BELOW, "Very cold at or below", True),
    ScheduleNumberSpec("very_cold_runtime", CONF_VERY_COLD_RUNTIME, "Very cold run time", False),
    ScheduleNumberSpec(
        "vent_above", CONF_VENT_ABOVE, "Ventilate at or above", True, ventilation=True
    ),
    ScheduleNumberSpec(
        "vent_runtime", CONF_VENT_RUNTIME, "Ventilation run time", False, ventilation=True
    ),
)


class EberspaecherScheduleNumber(EberspaecherScheduleEntity, NumberEntity):
    """A temperature or run time of the schedule."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: EberspaecherCoordinator,
        entry: EberspaecherConfigEntry,
        imei: str,
        spec: ScheduleNumberSpec,
    ) -> None:
        super().__init__(coordinator, entry, imei, f"schedule_{spec.key}")
        self._spec = spec
        self._attr_name = spec.name
        if spec.temperature:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_native_min_value = -50
            self._attr_native_max_value = 60
            self._attr_native_step = 0.5
        else:
            self._attr_device_class = NumberDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_native_min_value = 1
            self._attr_native_max_value = 600
            self._attr_native_step = 1

    @property
    def native_value(self) -> float:
        return getattr(self._config, self._spec.key)

    async def async_set_native_value(self, value: float) -> None:
        stored = float(value) if self._spec.temperature else int(value)
        async_set_schedule_options(self.hass, self._entry, **{self._spec.option: stored})
