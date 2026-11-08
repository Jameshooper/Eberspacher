"""A one-off "be ready by" time, for a day that is not the usual routine.

Set a date and time and the heater starts when it is needed for the car to be ready then.
It works like the schedule: the colder it is, the earlier it starts, by the same heating
bands. Unlike the schedule it ignores the weekday list, the pause date and where the phone
is (you asked for it on purpose), and it clears itself once it has run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import MODE_VENTILATION, STATE_ON
from .messages import text
from .schedule import choose_action, possible_runtimes

if TYPE_CHECKING:
    from .coordinator import EberspaecherCoordinator

_LOGGER = logging.getLogger(__name__)


class ReadyOnce:
    """Plans the wake-ups for one "be ready by" time and starts the heater when needed."""

    def __init__(self, hass: HomeAssistant, coordinator: EberspaecherCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.ready_at: datetime | None = None  # UTC
        self.last_result: str | None = None
        self._generation = 0  # bumped when the wake-ups are stopped
        self._lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    # --- listeners ---------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    # --- planning ----------------------------------------------------------

    @property
    def _cfg(self):
        # The heating bands, sensor and notify service are the schedule's.
        return self.coordinator.scheduler.config

    def plan(self) -> list[datetime]:
        """The moments to check the temperature, earliest first (UTC)."""
        if self.ready_at is None:
            return []
        runtimes = possible_runtimes(self._cfg)
        return sorted({self.ready_at - timedelta(minutes=r) for r in runtimes})

    @callback
    def async_set(self, value: datetime | None, restoring: bool = False) -> None:
        """Set, change or clear the time the car should be ready by."""
        self.async_stop()
        now = dt_util.utcnow()
        if value is not None:
            value = dt_util.as_utc(value).replace(second=0, microsecond=0)
        if value is None or value <= now:
            self.ready_at = None
            if value is not None and not restoring:
                self.last_result = text(self.hass, "result_once_expired")
            elif value is None:
                self.last_result = None
            self._notify_listeners()
            return
        self.ready_at = value
        self.last_result = None
        self._register()

    @callback
    def async_replan(self) -> None:
        """The options changed (for example the heating bands): plan again."""
        if self.ready_at is not None:
            self.async_stop()
            self._register()

    @callback
    def _register(self) -> None:
        now = dt_util.utcnow()
        plan = self.plan()
        for moment in plan:
            if moment > now:
                self._unsubs.append(
                    async_track_point_in_utc_time(self.hass, self._make_handler(), moment)
                )
        if plan and plan[0] <= now:
            # The earliest start has already passed: look at the temperature now.
            self.hass.async_create_task(self.async_wake())
        self._notify_listeners()

    @callback
    def async_stop(self) -> None:
        self._generation += 1
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    def _make_handler(self):
        async def _handler(_now: datetime) -> None:
            await self.async_wake()

        return _handler

    # --- waking ------------------------------------------------------------

    def _running(self) -> bool:
        return any(
            heater.get("state") == STATE_ON
            for device in self.coordinator.scheduler._devices().values()
            for heater in device.heaters.values()
        )

    @callback
    def _finish(self, result: str) -> None:
        self.ready_at = None
        self.last_result = result
        self.async_stop()
        self._notify_listeners()

    async def async_wake(self) -> None:
        async with self._lock:
            await self._async_wake()

    async def _async_wake(self) -> None:
        ready = self.ready_at
        if ready is None:
            return
        now = dt_util.utcnow()
        if now >= ready:
            self._finish(text(self.hass, "result_once_expired"))
            return
        if self._running():
            self._finish(text(self.hass, "result_once_running"))
            return

        scheduler = self.coordinator.scheduler
        cfg = self._cfg
        generation = self._generation
        # With the phone away from home the car is where the weather is that counts, so it is
        # asked itself (an old reading may be hours old, so only a fresh answer is used).
        # At home, or when the phone's location is not known, the home sensor comes first.
        place = self.coordinator.learner.tracker_state()
        away = place is not None and place.strip().casefold() != "home"
        sensor = scheduler._entity_temperature(cfg.temperature_entity)
        temperature, source = None, None
        if away:
            temperature = await scheduler._car_reading()
            if temperature is None and sensor is not None:
                temperature, source = sensor, cfg.temperature_entity
        elif sensor is not None:
            temperature, source = sensor, cfg.temperature_entity
        else:
            temperature = await scheduler._car_reading()
        if generation != self._generation or self.ready_at != ready:
            return  # changed or cancelled while reading the temperature

        plan = self.plan()
        last_wakeup = bool(plan) and now >= plan[-1]
        action = choose_action(cfg, temperature)
        if action is None:
            if last_wakeup:
                await self._async_nothing_to_do(temperature)
            return
        if ready - timedelta(minutes=action.runtime) > now:
            return  # this temperature's start time is still ahead; a later wake-up decides

        temp = f"{temperature:g}"
        if source:
            reading = text(self.hass, "reading_sensor", temp=temp, source=source)
        else:
            reading = text(self.hass, "reading_heater", temp=temp)
        kind = "ventilation" if action.mode == MODE_VENTILATION else "heating"
        local_now = dt_util.as_local(now)

        started, failed = await scheduler._start_all(action)
        if failed and not started:
            result = text(self.hass, "result_failed", reason=failed[0])
            await scheduler._notify(
                text(self.hass, "failed_title"),
                text(self.hass, "failed_body", reason=failed[0]),
            )
            if last_wakeup:
                self._finish(result)
            else:
                self.last_result = result
                self._notify_listeners()
            return
        if not started:
            self._finish(text(self.hass, "result_once_running"))
            return
        result = text(
            self.hass,
            f"result_started_{kind}",
            minutes=action.runtime,
            time=local_now.strftime("%H:%M"),
        )
        self._finish(result)
        await scheduler._notify(
            text(self.hass, f"started_title_{kind}"),
            text(
                self.hass,
                f"started_body_{kind}",
                minutes=action.runtime,
                ready=dt_util.as_local(ready).strftime("%H:%M"),
                reading=reading,
            ),
        )

    async def _async_nothing_to_do(self, temperature: float | None) -> None:
        scheduler = self.coordinator.scheduler
        if temperature is None:
            self._finish(text(self.hass, "result_no_reading"))
            await scheduler._notify(
                text(self.hass, "no_reading_title"), text(self.hass, "no_reading_body")
            )
            return
        temp = f"{temperature:g}"
        self._finish(text(self.hass, "result_not_needed", temp=temp))
        await scheduler._notify(
            text(self.hass, "not_needed_title"), text(self.hass, "not_needed_body", temp=temp)
        )
