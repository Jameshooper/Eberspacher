"""Temperature-based start schedule for Eberspächer EasyStart Web.

The plan is simple: the car should be ready at a fixed time, and the colder it
is the earlier the heater has to start. Each temperature band has its own run
time, so each band has its own start time (ready-by minus run time). The
manager wakes at every start time, reads the temperature and starts the heater
only if that wake-up belongs to the band the temperature falls in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COLD_BELOW,
    CONF_COLD_RUNTIME,
    CONF_MILD_RUNTIME,
    CONF_NO_HEAT_ABOVE,
    CONF_NOTIFY_SERVICE,
    CONF_READY_BY,
    CONF_SCHEDULE_ENABLED,
    CONF_TEMPERATURE_ENTITY,
    CONF_VENT_ABOVE,
    CONF_VENT_ENABLED,
    CONF_VENT_RUNTIME,
    CONF_VERY_COLD_BELOW,
    CONF_VERY_COLD_RUNTIME,
    CONF_WEEKDAYS,
    DOMAIN,
    MODE_HEATING,
    MODE_VENTILATION,
    STATE_ON,
    WEEKDAYS,
)
from .util import async_notify, parse_temperature

if TYPE_CHECKING:
    from .coordinator import EberspaecherCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_START = "start_heating"


@dataclass(frozen=True)
class ScheduleConfig:
    """The schedule options with defaults filled in."""

    enabled: bool = False
    ready_by: time = time(8, 10)
    weekdays: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")
    temperature_entity: str | None = None
    notify_service: str | None = None
    no_heat_above: float = 10
    mild_runtime: int = 40
    cold_below: float = 0
    cold_runtime: int = 70
    very_cold_below: float = -10
    very_cold_runtime: int = 100
    vent_enabled: bool = False
    vent_above: float = 25
    vent_runtime: int = 20

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> ScheduleConfig:
        """Build the config from stored options, ignoring anything unusable."""
        defaults = cls()
        values: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            key = _OPTION_KEYS.get(name, name)
            if key in options and options[key] not in (None, ""):
                values[name] = options[key]
        try:
            ready = values.get("ready_by", defaults.ready_by)
            if isinstance(ready, str):
                ready = time.fromisoformat(ready)
            weekdays = tuple(
                d for d in values.get("weekdays", defaults.weekdays) if d in WEEKDAYS
            )
            return cls(
                enabled=bool(values.get("enabled", defaults.enabled)),
                ready_by=ready,
                weekdays=weekdays,
                temperature_entity=values.get("temperature_entity"),
                notify_service=values.get("notify_service"),
                no_heat_above=float(values.get("no_heat_above", defaults.no_heat_above)),
                mild_runtime=int(values.get("mild_runtime", defaults.mild_runtime)),
                cold_below=float(values.get("cold_below", defaults.cold_below)),
                cold_runtime=int(values.get("cold_runtime", defaults.cold_runtime)),
                very_cold_below=float(
                    values.get("very_cold_below", defaults.very_cold_below)
                ),
                very_cold_runtime=int(
                    values.get("very_cold_runtime", defaults.very_cold_runtime)
                ),
                vent_enabled=bool(values.get("vent_enabled", defaults.vent_enabled)),
                vent_above=float(values.get("vent_above", defaults.vent_above)),
                vent_runtime=int(values.get("vent_runtime", defaults.vent_runtime)),
            )
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring unusable schedule options")
            return defaults


# Dataclass field -> option key, where they differ.
_OPTION_KEYS = {"enabled": CONF_SCHEDULE_ENABLED}
for _name, _key in (
    ("ready_by", CONF_READY_BY),
    ("weekdays", CONF_WEEKDAYS),
    ("temperature_entity", CONF_TEMPERATURE_ENTITY),
    ("notify_service", CONF_NOTIFY_SERVICE),
    ("no_heat_above", CONF_NO_HEAT_ABOVE),
    ("mild_runtime", CONF_MILD_RUNTIME),
    ("cold_below", CONF_COLD_BELOW),
    ("cold_runtime", CONF_COLD_RUNTIME),
    ("very_cold_below", CONF_VERY_COLD_BELOW),
    ("very_cold_runtime", CONF_VERY_COLD_RUNTIME),
    ("vent_enabled", CONF_VENT_ENABLED),
    ("vent_above", CONF_VENT_ABOVE),
    ("vent_runtime", CONF_VENT_RUNTIME),
):
    _OPTION_KEYS[_name] = _key


@dataclass(frozen=True)
class Action:
    """What to do for a given temperature."""

    mode: str
    runtime: int


def choose_action(cfg: ScheduleConfig, temperature: float | None) -> Action | None:
    """The action for this temperature, or None when nothing is needed or it is unknown."""
    if temperature is None:
        return None
    if cfg.vent_enabled and temperature >= cfg.vent_above:
        return Action(MODE_VENTILATION, cfg.vent_runtime)
    if temperature > cfg.no_heat_above:
        return None
    if temperature > cfg.cold_below:
        return Action(MODE_HEATING, cfg.mild_runtime)
    if temperature > cfg.very_cold_below:
        return Action(MODE_HEATING, cfg.cold_runtime)
    return Action(MODE_HEATING, cfg.very_cold_runtime)


def start_time(cfg: ScheduleConfig, runtime: int) -> time:
    """When a run of this length has to start to be ready by the set time."""
    ready = datetime.combine(datetime(2000, 1, 1), cfg.ready_by)
    return (ready - timedelta(minutes=runtime)).time().replace(second=0, microsecond=0)


def possible_runtimes(cfg: ScheduleConfig) -> set[int]:
    """Every run time the schedule can pick."""
    runtimes = {cfg.mild_runtime, cfg.cold_runtime, cfg.very_cold_runtime}
    if cfg.vent_enabled:
        runtimes.add(cfg.vent_runtime)
    return runtimes


def longest_runtime_fits(cfg: ScheduleConfig) -> bool:
    """Whether the longest run still starts after midnight on the same day."""
    minutes = cfg.ready_by.hour * 60 + cfg.ready_by.minute
    return max(possible_runtimes(cfg)) <= minutes


def start_times(cfg: ScheduleConfig) -> list[time]:
    """All wake-up times, earliest first."""
    return sorted({start_time(cfg, r) for r in possible_runtimes(cfg)})


def validate(cfg: ScheduleConfig) -> str | None:
    """An error key when the settings do not make sense together, else None."""
    if not cfg.no_heat_above > cfg.cold_below > cfg.very_cold_below:
        return "bands_order"
    if cfg.vent_enabled and cfg.vent_above <= cfg.no_heat_above:
        return "vent_below_heating"
    if not longest_runtime_fits(cfg):
        return "runtime_too_long"
    return None


class ScheduleManager:
    """Wakes at the start times and starts the heater when the temperature calls for it."""

    def __init__(
        self, hass: HomeAssistant, coordinator: EberspaecherCoordinator
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.config = ScheduleConfig()
        self.last_result: str | None = None
        self._last_start_date = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Tell listeners (the Schedule switch) when the config or result changes."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def async_configure(self, options: dict[str, Any]) -> None:
        """(Re)build the wake-ups from the options."""
        self.async_stop()
        self.config = ScheduleConfig.from_options(options)
        if self.config.enabled and self.config.weekdays:
            for t in start_times(self.config):
                self._unsubs.append(
                    async_track_time_change(
                        self.hass,
                        self._make_handler(t),
                        hour=t.hour,
                        minute=t.minute,
                        second=0,
                    )
                )
        self._notify_listeners()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    def _make_handler(self, at: time):
        async def _handler(now: datetime) -> None:
            await self.async_run(at, now)

        return _handler

    def _temperature(self) -> tuple[float | None, str]:
        """The outside temperature and where it came from."""
        cfg = self.config
        if cfg.temperature_entity:
            state = self.hass.states.get(cfg.temperature_entity)
            if state is not None and state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                value = parse_temperature(state.state)
                if value is not None:
                    if state.attributes.get("unit_of_measurement") == "°F":
                        value = round((value - 32) * 5 / 9, 1)
                    return value, cfg.temperature_entity
        for device in (self.coordinator.data or {}).values():
            for heater in device.heaters.values():
                value = parse_temperature(heater.get("lastMeasuredTemperature"))
                if value is not None:
                    return value, "the heater's last reading"
        return None, "no reading"

    async def async_run(self, at: time, now: datetime) -> None:
        """One wake-up at start time `at`."""
        cfg = self.config
        local = dt_util.as_local(now)
        if not cfg.enabled or WEEKDAYS[local.weekday()] not in cfg.weekdays:
            return
        if self._last_start_date == local.date():
            return

        temperature, source = self._temperature()
        action = choose_action(cfg, temperature)
        if action is None:
            if at == start_times(cfg)[-1]:
                if temperature is None:
                    self.last_result = "Not started: no temperature reading"
                    await self._notify(
                        "Car heater not started",
                        "There is no temperature reading, so the schedule did not "
                        "start the heater. Check the temperature sensor.",
                    )
                else:
                    self.last_result = f"Not needed ({temperature:g} °C)"
                    await self._notify(
                        "Car heater not needed today",
                        f"It is {temperature:g} °C, so the heater was not started.",
                    )
                self._notify_listeners()
            return
        if start_time(cfg, action.runtime) != at:
            return

        reading = f"{temperature:g} °C from {source}."
        label = "Ventilation" if action.mode == MODE_VENTILATION else "Heating"
        ready = cfg.ready_by.strftime("%H:%M")

        started, failed = await self._start_all(action)
        if failed and not started:
            self.last_result = f"Failed: {failed[0]}"
            await self._notify(
                "Car heater failed to start",
                f"{failed[0]}. Start it from the EasyStart app or the car.",
            )
        elif started:
            self._last_start_date = local.date()
            self.last_result = (
                f"{label} {action.runtime} min at {local.strftime('%H:%M')}"
            )
            await self._notify(
                f"Car {label.lower()} started",
                f"{label} for {action.runtime} min, ready by {ready}. {reading}",
            )
        self._notify_listeners()

    async def _start_all(self, action: Action) -> tuple[int, list[str]]:
        """Start every heater that is idle. Returns (started count, error texts)."""
        registry = er.async_get(self.hass)
        started = 0
        errors: list[str] = []
        for imei, device in (self.coordinator.data or {}).items():
            for address, heater in device.heaters.items():
                if heater.get("state") == STATE_ON:
                    continue
                entity_id = registry.async_get_entity_id(
                    "switch", DOMAIN, f"{imei}_{address}_heating"
                )
                if entity_id is None:
                    continue
                try:
                    await self.hass.services.async_call(
                        DOMAIN,
                        SERVICE_START,
                        {"runtime": action.runtime, "mode": action.mode},
                        target={"entity_id": entity_id},
                        blocking=True,
                    )
                    started += 1
                except HomeAssistantError as err:
                    errors.append(str(err))
        return started, errors

    async def _notify(self, title: str, message: str) -> None:
        await async_notify(self.hass, self.config.notify_service, title, message)
