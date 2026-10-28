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
    MODE_HEATING,
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
                    imei=imei, name=str(name), heaters=_index_heaters(imei, heaters)
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

    def _capabilities(self, imei: str, address: int) -> list[dict[str, Any]]:
        device = (self.data or {}).get(imei)
        heater = device.heaters.get(address, {}) if device else {}
        caps = heater.get("capabilities")
        return [c for c in caps if isinstance(c, dict)] if isinstance(caps, list) else []

    def runtime_limits(self, imei: str, address: int) -> tuple[int, int, int]:
        """Return (min, max, default) run time in minutes for a heater."""
        for cap in self._capabilities(imei, address):
            tr = cap.get("targetRuntime")
            if cap.get("operationMode") == MODE_HEATING and isinstance(tr, dict):
                low = _to_int(tr.get("minimumValue"), DEFAULT_MIN_RUNTIME)
                high = _to_int(tr.get("maximumValue"), DEFAULT_MAX_RUNTIME)
                if high < low:
                    low, high = DEFAULT_MIN_RUNTIME, DEFAULT_MAX_RUNTIME
                default = min(max(_to_int(tr.get("defaultValue"), DEFAULT_RUNTIME), low), high)
                return low, high, default
        return DEFAULT_MIN_RUNTIME, DEFAULT_MAX_RUNTIME, DEFAULT_RUNTIME

    def supports_mode(self, imei: str, address: int, mode: str) -> bool:
        """Whether the heater lists this operation mode (True when it lists none)."""
        modes = {c.get("operationMode") for c in self._capabilities(imei, address)}
        return not modes or mode in modes

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


def _to_int(value: Any, default: int) -> int:
    """Return value as a positive int, or the default if it is missing or unusable."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _index_heaters(imei: str, heaters: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Key heaters by bus address, skipping (and logging) any that have no usable one."""
    indexed: dict[int, dict[str, Any]] = {}
    for idx, heater in enumerate(heaters):
        raw = heater.get("address", idx + 1)
        try:
            address = int(raw)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Skipping heater on device ending %s with unusable address %r",
                imei[-4:],
                raw,
            )
            continue
        indexed[address] = heater
    return indexed
