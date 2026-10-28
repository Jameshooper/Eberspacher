"""Sensors for Eberspächer EasyStart Web."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EberspaecherConfigEntry
from .entity import EberspaecherEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the status, remaining-time and temperature sensors per heater."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for imei, device in coordinator.data.items():
        for address in device.heaters:
            entities.append(EberspaecherStatusSensor(coordinator, imei, address))
            entities.append(EberspaecherRemainingSensor(coordinator, imei, address))
            entities.append(EberspaecherTemperatureSensor(coordinator, imei, address))
    async_add_entities(entities)


class EberspaecherStatusSensor(EberspaecherEntity, SensorEntity):
    """Raw heater state reported by the cloud (e.g. OFF, ACTIVATION_REQUESTED)."""

    _attr_name = "Status"
    _attr_icon = "mdi:information-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, imei: str, address: int) -> None:
        super().__init__(coordinator, imei, address, "status")

    @property
    def native_value(self) -> str | None:
        return self.heater.get("heaterState")


class EberspaecherRemainingSensor(EberspaecherEntity, SensorEntity):
    """Minutes left of the current run."""

    _attr_name = "Remaining run time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, imei: str, address: int) -> None:
        super().__init__(coordinator, imei, address, "remaining")

    @property
    def native_value(self) -> int:
        operation = self.heater.get("currentOperation") or {}
        remaining = operation.get("remainingRuntime")
        return int(remaining) if isinstance(remaining, (int, float)) else 0


class EberspaecherTemperatureSensor(EberspaecherEntity, SensorEntity):
    """Last temperature the device reported (updated by the Request temperature button)."""

    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, imei: str, address: int) -> None:
        super().__init__(coordinator, imei, address, "temperature")

    @property
    def native_value(self) -> float | None:
        value = self.heater.get("lastMeasuredTemperature")
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
