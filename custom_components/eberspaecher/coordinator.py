"""Data update coordinator for Eberspächer EasyStart Web."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EberspaecherApi,
    EberspaecherAuthError,
    EberspaecherConnectionError,
)
from .const import (
    CALLS_CACHE_SECONDS,
    DEFAULT_MAX_RUNTIME,
    DEFAULT_MIN_RUNTIME,
    DEFAULT_RUNTIME,
    DOMAIN,
    STATE_ON,
    UPDATE_INTERVAL_ACTIVE,
    UPDATE_INTERVAL_IDLE,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class EberspaecherDevice:
    """One registered device (a modem/controller) and its heaters."""

    imei: str
    name: str
    heaters: dict[int, dict[str, Any]] = field(default_factory=dict)


class EberspaecherCoordinator(DataUpdateCoordinator[dict[str, EberspaecherDevice]]):
    """Fetch devices and heater state from the cloud."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: EberspaecherApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL_IDLE,
        )
        self.api = api
        self._calls: list[dict[str, Any]] = []
        self._calls_fetched_at = 0.0
        self._cancel_refresh: CALLBACK_TYPE | None = None
        # Run time the "Heating" switch uses, per (imei, heater address).
        self.runtimes: dict[tuple[str, int], int] = {}

    async def _async_update_data(self) -> dict[str, EberspaecherDevice]:
        try:
            if not self._calls or time.monotonic() - self._calls_fetched_at > CALLS_CACHE_SECONDS:
                self._calls = await self.api.async_get_calls()
                self._calls_fetched_at = time.monotonic()

            result: dict[str, EberspaecherDevice] = {}
            for call in self._calls:
                imei = str(call["imei"])
                name = call.get("name") or call.get("description") or f"EasyStart {imei[-4:]}"
                heaters = await self.api.async_get_heaters(imei)
                result[imei] = EberspaecherDevice(
                    imei=imei,
                    name=str(name),
                    heaters={
                        int(h.get("address", idx + 1)): h
                        for idx, h in enumerate(heaters)
                    },
                )
        except EberspaecherAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EberspaecherConnectionError as err:
            raise UpdateFailed(str(err)) from err

        # Poll faster only while something is actually running.
        active = any(
            heater.get("state") == STATE_ON
            for device in result.values()
            for heater in device.heaters.values()
        )
        self.update_interval = UPDATE_INTERVAL_ACTIVE if active else UPDATE_INTERVAL_IDLE
        return result

    def runtime_limits(self, imei: str, address: int) -> tuple[int, int, int]:
        """Return (min, max, default) run time in minutes for a heater."""
        heater = (self.data or {}).get(imei, EberspaecherDevice(imei, "")).heaters.get(address, {})
        for cap in heater.get("capabilities") or []:
            if cap.get("operationMode") == "HEATING" and cap.get("targetRuntime"):
                tr = cap["targetRuntime"]
                return (
                    int(tr.get("minimumValue") or DEFAULT_MIN_RUNTIME),
                    int(tr.get("maximumValue") or DEFAULT_MAX_RUNTIME),
                    int(tr.get("defaultValue") or DEFAULT_RUNTIME),
                )
        return DEFAULT_MIN_RUNTIME, DEFAULT_MAX_RUNTIME, DEFAULT_RUNTIME

    @callback
    def async_schedule_refresh(self, delay: int) -> None:
        """Re-read the state shortly after a command (replaces any pending refresh)."""
        if self._cancel_refresh:
            self._cancel_refresh()

        async def _refresh(_now: Any) -> None:
            self._cancel_refresh = None
            await self.async_request_refresh()

        self._cancel_refresh = async_call_later(self.hass, delay, _refresh)

    async def async_shutdown(self) -> None:
        """Cancel any pending delayed refresh."""
        if self._cancel_refresh:
            self._cancel_refresh()
            self._cancel_refresh = None
        await super().async_shutdown()
