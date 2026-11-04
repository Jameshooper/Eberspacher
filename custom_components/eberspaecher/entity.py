"""Base entity for Eberspächer EasyStart Web."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EberspaecherCoordinator


class EberspaecherEntity(CoordinatorEntity[EberspaecherCoordinator]):
    """An entity that belongs to one heater (or to its device when address is None)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EberspaecherCoordinator,
        imei: str,
        address: int | None,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._imei = imei
        self._address = address
        suffix = f"_{address}" if address is not None else ""
        self._attr_unique_id = f"{imei}{suffix}_{key}"
        device = coordinator.data[imei]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, imei)},
            manufacturer="Eberspächer",
            name=device.name,
            serial_number=imei,
        )

    @property
    def available(self) -> bool:
        """Entity is available while the device is still reported by the cloud."""
        if not super().available:
            return False
        device = (self.coordinator.data or {}).get(self._imei)
        if device is None:
            return False
        return self._address is None or self._address in device.heaters

    @property
    def heater(self) -> dict[str, Any]:
        """The latest raw heater data (empty if unavailable)."""
        device = (self.coordinator.data or {}).get(self._imei)
        if device is None or self._address is None:
            return {}
        return device.heaters.get(self._address, {})


class EberspaecherScheduleEntity(EberspaecherEntity):
    """A schedule setting shown on the device page; the value lives in the entry options."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: EberspaecherCoordinator, entry: ConfigEntry, imei: str, key: str
    ) -> None:
        super().__init__(coordinator, imei, None, key)
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.scheduler.async_add_listener(self.async_write_ha_state)
        )

    @property
    def _config(self):
        return self.coordinator.scheduler.config
