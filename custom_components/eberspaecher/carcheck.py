"""A weekly check that the unit in the car still answers.

The integration talks to myeberspaecher.com every few minutes, but that only shows the
cloud is up. The unit in the car can stop answering while the cloud is fine: its SIM
expired, or the car battery is low. So once a week the car is asked for a temperature.
When it does not answer, you get a message and a notice under Repairs, before a cold
morning shows it. A temperature request only reads: it never starts the heater.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .api import EberspaecherConnectionError, EberspaecherError
from .const import CONF_CAR_CHECK_ENABLED, CONF_NOTIFY_SERVICE, DOMAIN, STATE_ON
from .messages import text
from .util import async_notify, parse_temperature

if TYPE_CHECKING:
    from .coordinator import EberspaecherCoordinator

_LOGGER = logging.getLogger(__name__)

CHECK_DAY = 6  # Sunday
CHECK_AT = time(18, 0)  # local time, before the working week
ANSWER_WAIT = 60  # seconds between looks for the car's answer
ANSWER_TRIES = 3  # looks before the car counts as not answering


@dataclass(frozen=True)
class CarCheckConfig:
    enabled: bool = True
    notify_service: str | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> CarCheckConfig:
        return cls(
            enabled=bool(options.get(CONF_CAR_CHECK_ENABLED, True)),
            notify_service=options.get(CONF_NOTIFY_SERVICE) or None,
        )


class CarCheck:
    """Asks the car for a temperature every Sunday evening."""

    def __init__(self, hass: HomeAssistant, coordinator: EberspaecherCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.config = CarCheckConfig()
        self.last_checked: datetime | None = None
        self.last_ok: bool | None = None
        self._generation = 0  # bumped on stop, so a check still waiting gives up
        self._lock = asyncio.Lock()
        self._unsub: Callable[[], None] | None = None

    @property
    def _issue_id(self) -> str:
        return f"car_unreachable_{self.coordinator.config_entry.entry_id}"

    @callback
    def async_configure(self, options: dict[str, Any]) -> None:
        self.async_stop()
        self.config = CarCheckConfig.from_options(options)
        if not self.config.enabled:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            return
        self._unsub = async_track_time_change(
            self.hass, self._async_timer, hour=CHECK_AT.hour, minute=CHECK_AT.minute, second=0
        )

    @callback
    def async_stop(self) -> None:
        self._generation += 1
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _async_timer(self, now: datetime) -> None:
        if dt_util.as_local(now).weekday() == CHECK_DAY:
            await self.async_check()

    @callback
    def observe(self, devices: dict[str, Any]) -> None:
        """The heater runs, so the car answers: a notice from the last check is out of date."""
        for device in devices.values():
            if any(h.get("state") == STATE_ON for h in device.heaters.values()):
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
                return

    def _reading(self, imei: str) -> Any:
        device = (self.coordinator.data or {}).get(imei)
        for heater in (device.heaters.values() if device else ()):
            raw = heater.get("lastMeasuredTemperature")
            if parse_temperature(raw) is not None:
                return raw
        return None

    def _running(self, imei: str) -> bool:
        device = (self.coordinator.data or {}).get(imei)
        return bool(device) and any(h.get("state") == STATE_ON for h in device.heaters.values())

    async def async_check(self) -> None:
        """Ask every car on the account for a temperature and report one that stays silent."""
        async with self._lock:
            silent: list[str] = []
            for imei, device in list((self.coordinator.data or {}).items()):
                answered = await self._async_check_one(imei)
                if answered is None:
                    return  # the cloud is down or the check was stopped: no verdict
                if not answered:
                    silent.append(device.name)
            self.last_checked = dt_util.utcnow()
            self.last_ok = not silent
            if not silent:
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
                return
            names = ", ".join(silent)
            _LOGGER.warning("The car did not answer the weekly check")
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="car_unreachable",
                translation_placeholders={"name": names},
            )
            await async_notify(
                self.hass,
                self.config.notify_service,
                text(self.hass, "car_check_title"),
                text(self.hass, "car_check_body", name=names),
            )

    async def _async_check_one(self, imei: str) -> bool | None:
        """True when the car answered, False when it did not, None when there is no verdict."""
        if self._running(imei):
            return True
        generation = self._generation
        before = self._reading(imei)
        try:
            await self.coordinator.api.async_request_temperature(imei)
        except EberspaecherConnectionError as err:
            _LOGGER.debug("Weekly car check skipped, the cloud is not reachable: %s", err)
            return None
        except EberspaecherError as err:
            _LOGGER.debug("Weekly car check: the request was refused: %s", err)
            return False
        cloud_answered = False
        for _ in range(ANSWER_TRIES):
            await asyncio.sleep(ANSWER_WAIT)
            if generation != self._generation:
                return None
            await self.coordinator.async_refresh()
            if not self.coordinator.last_update_success:
                continue
            cloud_answered = True
            raw = self._reading(imei)
            if raw is None:
                continue
            # With a timestamp only a new reading counts. Without one there is no way to
            # tell, so a reading after an accepted request counts as the answer.
            has_time = isinstance(raw, dict) and raw.get("timestamp") is not None
            if not has_time or raw != before:
                return True
        # Without a single good read from the cloud, the car was never really asked.
        return False if cloud_answered else None
