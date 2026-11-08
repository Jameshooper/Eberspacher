"""The one-off "ready by" time for Eberspächer EasyStart Web."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import EberspaecherConfigEntry
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the one-off time, once per account, on the schedule's device."""
    coordinator = entry.runtime_data
    first = coordinator.primary_imei
    if first is not None:
        async_add_entities([EberspaecherReadyOnce(coordinator, first)])


class EberspaecherReadyOnce(EberspaecherEntity, DateTimeEntity, RestoreEntity):
    """Be ready by this date and time, once: the heater starts when it is needed."""

    _attr_translation_key = "ready_once"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: EberspaecherCoordinator, imei: str) -> None:
        super().__init__(coordinator, imei, None, "ready_once")

    async def async_added_to_hass(self) -> None:
        """Keep the one-off after a restart, and listen for its progress."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.ready_once.async_add_listener(self.async_write_ha_state)
        )
        last = await self.async_get_last_state()
        if last is not None and (value := dt_util.parse_datetime(last.state)):
            if value.tzinfo is not None:
                self.coordinator.ready_once.async_set(value, restoring=True)

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.ready_once.ready_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        manager = self.coordinator.ready_once
        return {
            "start_times": [
                dt_util.as_local(moment).strftime("%H:%M") for moment in manager.plan()
            ],
            "last_result": manager.last_result,
        }

    async def async_set_value(self, value: datetime) -> None:
        self.coordinator.ready_once.async_set(value)
