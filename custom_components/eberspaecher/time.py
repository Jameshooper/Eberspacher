"""Ready-by time of the schedule for Eberspächer EasyStart Web."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EberspaecherConfigEntry
from .const import CONF_READY_BY
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherScheduleEntity
from .schedule import async_set_schedule_options


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the ready-by time to the first device."""
    coordinator = entry.runtime_data
    imei = next(iter(coordinator.data), None)
    if imei is not None:
        async_add_entities([EberspaecherReadyBy(coordinator, entry, imei)])


class EberspaecherReadyBy(EberspaecherScheduleEntity, TimeEntity):
    """When the car should be ready."""

    _attr_name = "Ready by"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        coordinator: EberspaecherCoordinator,
        entry: EberspaecherConfigEntry,
        imei: str,
    ) -> None:
        super().__init__(coordinator, entry, imei, "schedule_ready_by")

    @property
    def native_value(self) -> time:
        return self._config.ready_by

    async def async_set_value(self, value: time) -> None:
        async_set_schedule_options(
            self.hass, self._entry, **{CONF_READY_BY: value.strftime("%H:%M:%S")}
        )
