"""The ready-by times for Eberspächer EasyStart Web."""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EberspaecherConfigEntry
from .const import CONF_ALT_ENABLED, CONF_ALT_READY_BY, CONF_READY_BY, DOMAIN
from .coordinator import EberspaecherCoordinator
from .entity import EberspaecherEntity
from .schedule import ScheduleConfig, longest_runtime_fits


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EberspaecherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the two ready-by times, once per account, on the schedule's device."""
    coordinator = entry.runtime_data
    first = coordinator.primary_imei
    if first is not None:
        async_add_entities(
            [
                EberspaecherReadyBy(coordinator, entry, first, "ready_by", CONF_READY_BY),
                EberspaecherReadyBy(coordinator, entry, first, "alt_ready_by", CONF_ALT_READY_BY),
            ]
        )


class EberspaecherReadyBy(EberspaecherEntity, TimeEntity):
    """The time the car should be ready, for one group of days (kept in the options)."""

    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        coordinator: EberspaecherCoordinator,
        entry: ConfigEntry,
        imei: str,
        key: str,
        option: str,
    ) -> None:
        super().__init__(coordinator, imei, None, key)
        self._attr_translation_key = key
        self._entry = entry
        self._option = option

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.scheduler.async_add_listener(self.async_write_ha_state)
        )

    def _config(self) -> ScheduleConfig:
        return self.coordinator.scheduler.config

    @property
    def native_value(self) -> time | None:
        if self._option == CONF_ALT_READY_BY:
            # The time that was set, also while those days are off.
            stored = self._entry.options.get(CONF_ALT_READY_BY)
            try:
                return time.fromisoformat(stored) if stored else None
            except (TypeError, ValueError):
                return None
        return self._config().ready_by

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cfg = self._config()
        days = cfg.alt_weekdays if self._option == CONF_ALT_READY_BY else cfg.weekdays
        return {"weekdays": list(days)}

    async def async_set_value(self, value: time) -> None:
        value = value.replace(second=0, microsecond=0)
        options = {**self._entry.options, self._option: value.strftime("%H:%M:%S")}
        if self._option == CONF_ALT_READY_BY:
            options[CONF_ALT_ENABLED] = True  # setting a time turns those days on
        if not longest_runtime_fits(ScheduleConfig.from_options(options)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="runtime_too_long"
            )
        self.hass.config_entries.async_update_entry(self._entry, options=options)
