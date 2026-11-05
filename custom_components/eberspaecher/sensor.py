"""Sensors for Eberspächer EasyStart Web."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change

from . import EberspaecherConfigEntry
from .const import LEG_HOME, LEG_WORK, WEEKDAYS
from .entity import EberspaecherEntity
from .util import parse_temperature


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
    # The reminder is one per account, so its sensor goes on the first device only.
    first = next(iter(coordinator.data), None)
    if first is not None:
        entities.append(EberspaecherLastHeatedSensor(coordinator, first))
        entities.append(EberspaecherLearnedSensor(coordinator, first, LEG_HOME))
        entities.append(EberspaecherLearnedSensor(coordinator, first, LEG_WORK))
    async_add_entities(entities)


class EberspaecherStatusSensor(EberspaecherEntity, SensorEntity):
    """Raw heater state reported by the cloud (e.g. OFF, ACTIVATION_REQUESTED)."""

    _attr_translation_key = "status"
    _attr_icon = "mdi:information-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, imei: str, address: int) -> None:
        super().__init__(coordinator, imei, address, "status")

    @property
    def native_value(self) -> str | None:
        return self.heater.get("heaterState")


class EberspaecherRemainingSensor(EberspaecherEntity, SensorEntity):
    """Minutes left of the current run."""

    _attr_translation_key = "remaining"
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

    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, imei: str, address: int) -> None:
        super().__init__(coordinator, imei, address, "temperature")

    @property
    def native_value(self) -> float | None:
        return parse_temperature(self.heater.get("lastMeasuredTemperature"))


class EberspaecherLastHeatedSensor(EberspaecherEntity, SensorEntity):
    """When the heater last ran in heating mode, as seen by Home Assistant."""

    _attr_translation_key = "last_heated"
    _attr_icon = "mdi:history"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, imei: str) -> None:
        super().__init__(coordinator, imei, None, "last_heated")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.reminder.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.reminder.last_heated


class EberspaecherLearnedSensor(EberspaecherEntity, SensorEntity):
    """The ready-by time learned for today, with the learned time of every weekday."""

    _attr_icon = "mdi:school-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, imei: str, leg: str) -> None:
        super().__init__(coordinator, imei, None, f"learned_{leg}")
        self._attr_translation_key = f"learned_{leg}"
        self._leg = leg

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.learner.async_add_listener(self.async_write_ha_state)
        )
        # The state is today's time, so it changes at midnight even without polling.
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_new_day, hour=0, minute=0, second=10)
        )

    @callback
    def _async_new_day(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        learner = self.coordinator.learner
        if not learner.config.enabled:
            return None
        today = dt_util.now().date()
        scheduler = (
            self.coordinator.scheduler if self._leg == LEG_HOME else self.coordinator.work_scheduler
        )
        if learner.learned_minutes(self._leg, today.weekday(), today) is None:
            return None
        return scheduler.config_for(today).ready_by.strftime("%H:%M")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        learner = self.coordinator.learner
        today = dt_util.now().date()
        learned = {}
        for index, day in enumerate(WEEKDAYS):
            found = learner.learned_time(self._leg, index, today)
            learned[day] = found.strftime("%H:%M") if found else None
        return {
            "learned": learned,
            "samples": {
                day: len(learner.samples(self._leg, index, today))
                for index, day in enumerate(WEEKDAYS)
            },
        }
