"""Learn when you usually get in the car, so the car can be ready by then.

A sensor tells when you got in (for an iPhone, the Audio Output sensor switches to
CarPlay). The first such moment of a morning at home, and of an afternoon after hours
away from home (at work), is kept for each day. The time the car should be ready by is
then taken from the middle (median) of what that weekday looked like over the last weeks.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LEARN_ALTERNATE,
    CONF_LEARN_ENABLED,
    CONF_LEARN_ENTITY,
    CONF_LEARN_MARGIN,
    CONF_LEARN_STATE,
    CONF_LEARN_TRACKER,
    DEFAULT_LEARN_MARGIN,
    DEFAULT_LEARN_STATE,
    DOMAIN,
    LEG_HOME,
    LEG_WORK,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 30  # seconds
KEEP_DAYS = 120  # samples kept
LOOKBACK_DAYS = 56  # samples used
MIN_SAMPLES = 3  # same weekdays needed before the learned time is trusted
LEARNED_CHANGE = 5  # minutes a learned time must move to be worth a message
MAX_SHIFT = 120  # minutes the learned time may differ from the fixed one
# Hours of the day in which a first connection counts as leaving home / leaving work.
WINDOWS = {LEG_HOME: (4, 12), LEG_WORK: (13, 23)}
# A phone often leaves the home zone a few minutes before it connects to the car.
RECENTLY_HOME = timedelta(minutes=30)
# An afternoon connection is leaving work only after this long away from home. A shorter
# outing (the way back from the shop on a day off) is not.
AWAY_BEFORE_WORK = timedelta(hours=3)
# Bump when the rules for reading history change, so existing history is read again.
HISTORY_RULES = "3"


@dataclass(frozen=True)
class LearnConfig:
    """The learning options with defaults filled in."""

    enabled: bool = False
    entity: str | None = None
    state: str = DEFAULT_LEARN_STATE
    tracker: str | None = None
    margin: int = DEFAULT_LEARN_MARGIN
    alternate: bool = False

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> LearnConfig:
        try:
            margin = int(options.get(CONF_LEARN_MARGIN, DEFAULT_LEARN_MARGIN))
        except (TypeError, ValueError):
            margin = DEFAULT_LEARN_MARGIN
        return cls(
            enabled=bool(options.get(CONF_LEARN_ENABLED, False)),
            entity=options.get(CONF_LEARN_ENTITY) or None,
            state=str(options.get(CONF_LEARN_STATE) or DEFAULT_LEARN_STATE),
            tracker=options.get(CONF_LEARN_TRACKER) or None,
            margin=max(0, margin),
            alternate=bool(options.get(CONF_LEARN_ALTERNATE, False)),
        )


def week_parity(day: date) -> int:
    """0 or 1, switching every Monday, so odd and even weeks can be told apart."""
    return ((day.toordinal() - 1) // 7) % 2


def classify(
    local: datetime,
    tracker_state: str | None,
    recently_home: bool = False,
    away_for: timedelta | None = None,
) -> str | None:
    """Whether a connection at this time and place is leaving home, leaving work or neither.

    Leaving home is the morning with the phone at home or just gone from home. Leaving
    work is the afternoon with the phone away from home for hours, wherever work is.
    `away_for` is how long the phone has been away (None when that is not known).
    """
    if not tracker_state:
        return None
    home = recently_home or tracker_state.strip().casefold() == "home"
    kind = LEG_HOME if home else LEG_WORK
    if kind == LEG_WORK and away_for is not None and away_for < AWAY_BEFORE_WORK:
        return None
    first, last = WINDOWS[kind]
    return kind if first <= local.hour < last else None


def _home_within(
    tracker: list[tuple[datetime, str]], times: list[datetime], when: datetime
) -> bool:
    """Whether the phone was at home at any moment in the half hour up to `when`."""
    start = when - RECENTLY_HOME
    index = bisect_right(times, start) - 1
    if index >= 0 and tracker[index][1].strip().casefold() == "home":
        return True
    low = bisect_right(times, start)
    high = bisect_right(times, when)
    return any(state.strip().casefold() == "home" for _, state in tracker[low:high])


def _left_home(
    tracker: list[tuple[datetime, str]], times: list[datetime], when: datetime
) -> datetime | None:
    """When the phone last left home before `when` (None if it was not home in the history)."""
    index = bisect_right(times, when) - 1
    if index >= 0 and tracker[index][1].strip().casefold() == "home":
        return when
    while index >= 0:
        if tracker[index][1].strip().casefold() == "home":
            return times[index + 1]
        index -= 1
    return None


def samples_from_history(
    signal: list[tuple[datetime, str]],
    tracker: list[tuple[datetime, str]],
    signal_state: str,
    tz: tzinfo,
) -> dict[str, dict[str, int]]:
    """The first leaving-home and leaving-work moment of each day in recorded history."""
    tracker = sorted(tracker, key=lambda item: item[0])
    times = [t for t, _ in tracker]
    wanted = signal_state.casefold()
    samples: dict[str, dict[str, int]] = {LEG_HOME: {}, LEG_WORK: {}}
    ordered = sorted(signal, key=lambda item: item[0])
    # The first entry is the state the window began with, not a connection.
    previous = ordered[0][1] if ordered else None
    for when, state in ordered[1:]:
        entered = state.casefold() == wanted and previous.casefold() != wanted
        previous = state
        if not entered:
            continue
        index = bisect_right(times, when) - 1
        place = tracker[index][1] if index >= 0 else None
        local = when.astimezone(tz)
        left = _left_home(tracker, times, when)
        away_for = None if left is None else when - left
        kind = classify(local, place, _home_within(tracker, times, when), away_for)
        if kind is None:
            continue
        day = local.date().isoformat()
        samples[kind].setdefault(day, local.hour * 60 + local.minute)
    return samples


def _valid_samples(raw: Any) -> dict[str, int]:
    """Stored samples, skipping anything that is not a real day and minute of the day."""
    if not isinstance(raw, dict):
        return {}
    samples: dict[str, int] = {}
    for day, minutes in raw.items():
        try:
            date.fromisoformat(str(day))
            value = int(minutes)
        except (TypeError, ValueError):
            continue
        if 0 <= value < 24 * 60:
            samples[str(day)] = value
    return samples


def _round_down(minutes: int, step: int = 5) -> int:
    return minutes - minutes % step


class DepartureLearner:
    """Records the first time you get in the car each morning and afternoon."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.config = LearnConfig()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.departures"
        )
        self._samples: dict[str, dict[str, int]] = {LEG_HOME: {}, LEG_WORK: {}}
        self._backfilled: str | None = None
        self._unsub: Callable[[], None] | None = None
        self._last_home: datetime | None = None  # when the phone was last seen at home
        self._listeners: list[Callable[[], None]] = []
        # Told when a new or changed time has been learned: (leg, day it applies to, minutes).
        self.on_learned: Callable[[str, date, int], None] | None = None

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        samples = stored.get("samples") if isinstance(stored, dict) else None
        if not isinstance(samples, dict):
            samples = {}
        for kind in self._samples:
            self._samples[kind] = _valid_samples(samples.get(kind))
        self._backfilled = stored.get("backfilled") if isinstance(stored, dict) else None
        if isinstance(stored, dict) and isinstance(last_home := stored.get("last_home"), str):
            try:
                self._last_home = datetime.fromisoformat(last_home)
            except ValueError:
                pass

    def _data(self) -> dict[str, Any]:
        cutoff = (dt_util.now().date() - timedelta(days=KEEP_DAYS)).isoformat()
        samples = {
            kind: {d: m for d, m in days.items() if d >= cutoff}
            for kind, days in self._samples.items()
        }
        return {
            "samples": samples,
            "backfilled": self._backfilled,
            "last_home": self._last_home.isoformat() if self._last_home else None,
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def async_configure(self, options: dict[str, Any]) -> None:
        """(Re)start listening from the options."""
        self._cancel()
        self.config = LearnConfig.from_options(options)
        cfg = self.config
        if cfg.enabled and cfg.entity and cfg.tracker:
            self._unsub = async_track_state_change_event(
                self.hass, [cfg.entity, cfg.tracker], self._async_changed
            )
            if (self.tracker_state() or "").strip().casefold() == "home":
                self._last_home = dt_util.utcnow()
            elif self._last_home is None and (state := self.hass.states.get(cfg.tracker)):
                # Not seen at home yet: away at least since the tracker last changed.
                self._last_home = state.last_changed
            self.hass.async_create_task(self.async_backfill())
        self._notify_listeners()

    def _cancel(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def async_shutdown(self) -> None:
        self._cancel()
        await self._store.async_save(self._data())

    # --- recording ---------------------------------------------------------

    def tracker_state(self) -> str | None:
        tracker = self.config.tracker
        state = self.hass.states.get(tracker) if tracker else None
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state

    def away_for(self, now: datetime | None = None) -> timedelta | None:
        """How long the phone has been away from home (None when not known)."""
        if (self.tracker_state() or "").strip().casefold() == "home":
            return timedelta(0)
        if self._last_home is None:
            return None
        return (now or dt_util.utcnow()) - self._last_home

    def recently_home(self, now: datetime | None = None) -> bool:
        """Whether the phone is at home or left home within the last half hour."""
        if (self.tracker_state() or "").strip().casefold() == "home":
            return True
        if self._last_home is None:
            return False
        return (now or dt_util.utcnow()) - self._last_home <= RECENTLY_HOME

    @callback
    def _async_changed(self, event: Event) -> None:
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if event.data.get("entity_id") == self.config.tracker:
            # Remember the last moment at home, also when the phone has just left.
            if old is not None and old.state.strip().casefold() == "home":
                self._last_home = dt_util.utcnow()
                self._save()
            return
        if new is None or new.state.casefold() != self.config.state.casefold():
            return
        if old is not None and old.state.casefold() == new.state.casefold():
            return
        self.record(dt_util.as_local(new.last_changed), self.tracker_state())

    @callback
    def record(self, local: datetime, tracker_state: str | None) -> None:
        """Keep this moment when it is the first of its kind that day."""
        kind = classify(local, tracker_state, self.recently_home(), self.away_for())
        if kind is None:
            return
        day = local.date().isoformat()
        if day in self._samples[kind]:
            return
        # The next time this weekday comes round (two weeks on when weeks alternate).
        target = local.date() + timedelta(days=14 if self.config.alternate else 7)
        before = self.learned_minutes(kind, local.weekday(), target)
        self._samples[kind][day] = local.hour * 60 + local.minute
        after = self.learned_minutes(kind, local.weekday(), target)
        self._save()
        self._notify_listeners()
        if (
            after is not None
            and (before is None or abs(after - before) >= LEARNED_CHANGE)
            and self.on_learned is not None
        ):
            self.on_learned(kind, target, after)

    # --- history -----------------------------------------------------------

    async def _async_fetch_history(
        self, start: datetime, end: datetime, entity_ids: list[str]
    ) -> dict[str, list[tuple[datetime, str]]]:
        """Recorded states as (time, state) per entity."""
        if "recorder" not in self.hass.config.components:
            return {}
        from homeassistant.components.recorder import get_instance, history

        states = await get_instance(self.hass).async_add_executor_job(
            lambda: history.get_significant_states(
                self.hass,
                start,
                end,
                entity_ids,
                include_start_time_state=True,
                significant_changes_only=False,
                no_attributes=True,
            )
        )
        return {
            entity_id: [(s.last_changed, s.state) for s in items]
            for entity_id, items in states.items()
        }

    async def async_backfill(self) -> None:
        """Learn from the history Home Assistant already has, once per setup of the options."""
        cfg = self.config
        key = f"{HISTORY_RULES}|{cfg.entity}|{cfg.state}|{cfg.tracker}"
        if not (cfg.entity and cfg.tracker) or self._backfilled == key:
            return
        end = dt_util.utcnow()
        start = end - timedelta(days=LOOKBACK_DAYS)
        try:
            history = await self._async_fetch_history(start, end, [cfg.entity, cfg.tracker])
        except Exception:  # noqa: BLE001 - history is a bonus, never a reason to fail
            _LOGGER.debug("Could not read the history to learn from", exc_info=True)
            return
        found = samples_from_history(
            history.get(cfg.entity, []),
            history.get(cfg.tracker, []),
            cfg.state,
            dt_util.get_default_time_zone(),
        )
        signal, tracker = history.get(cfg.entity), history.get(cfg.tracker)
        if signal and tracker:
            # Where history covers a day, it replaces what was kept, which may have been
            # found under older rules. Days before what history holds are kept as they are.
            covered = max(start, signal[0][0], tracker[0][0])
            after = covered.astimezone(dt_util.get_default_time_zone()).date().isoformat()
            for kind, days in found.items():
                kept = {d: m for d, m in self._samples[kind].items() if d <= after}
                self._samples[kind] = {**kept, **{d: m for d, m in days.items() if d > after}}
        else:
            for kind, days in found.items():
                for day, minutes in days.items():
                    self._samples[kind].setdefault(day, minutes)
        self._backfilled = key
        self._save()
        self._notify_listeners()

    # --- what was learned --------------------------------------------------

    def samples(self, kind: str, weekday: int, today: date | None = None) -> list[int]:
        """Recent departure minutes for one weekday (Monday is 0), earliest first.

        Only days before `today` count, so leaving today does not move today's plan.
        """
        today = today or dt_util.now().date()
        cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
        alternate = self.config.alternate
        return sorted(
            minutes
            for day, minutes in self._samples[kind].items()
            if cutoff <= day < today.isoformat()
            and date.fromisoformat(day).weekday() == weekday
            # With alternating weeks only the same kind of week counts.
            and (not alternate or week_parity(date.fromisoformat(day)) == week_parity(today))
        )

    def learned_minutes(self, kind: str, weekday: int, today: date | None = None) -> int | None:
        """Minutes after midnight the car should be ready by, or None while there is too little to go on."""
        values = self.samples(kind, weekday, today)
        if len(values) < MIN_SAMPLES:
            return None
        # The middle day (the earlier of the two middle ones for an even count). One unusually
        # early day does not pull the time forward, which would only heat a car that has
        # cooled again by the time you usually leave.
        middle = values[(len(values) - 1) // 2]
        return _round_down(max(0, middle - self.config.margin))

    def learned_time(self, kind: str, weekday: int, today: date | None = None) -> time | None:
        minutes = self.learned_minutes(kind, weekday, today)
        return None if minutes is None else time(minutes // 60, minutes % 60)
