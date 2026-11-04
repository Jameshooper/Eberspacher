"""Pause until date for Eberspächer EasyStart Web."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EberspaecherConfigEntry
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the pause date, once per account, on the first device."""
    coordinator = entry.runtime_data
    first = next(iter(coordinator.data), None)
    if first is not None:
        async_add_entities([EberspaecherPauseUntil(coordinator, first)])


class EberspaecherPauseUntil(EberspaecherEntity, DateEntity, RestoreEntity):
    """The schedules do nothing up to and including this day, for example while the car is not used."""

    _attr_translation_key = "pause_until"
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, coordinator: EberspaecherCoordinator, imei: str) -> None:
        super().__init__(coordinator, imei, None, "pause_until")

    async def async_added_to_hass(self) -> None:
        """Restore the pause after a restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            try:
                self.coordinator.paused_until = date.fromisoformat(last.state)
            except ValueError:
                pass

    @property
    def native_value(self) -> date | None:
        return self.coordinator.paused_until

    async def async_set_value(self, value: date) -> None:
        self.coordinator.paused_until = value
        self.async_write_ha_state()
