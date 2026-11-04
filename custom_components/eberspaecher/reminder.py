"""Reminder to run the heater now and then.

A heater that sits unused for months (the summer) should be run once in a while so
the fuel lines and pump stay in working order. The manager remembers when the heater
last ran in heating mode and, once it has been idle for the chosen number of days,
sends a message and raises a repair notice until it runs again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NOTIFY_SERVICE,
    CONF_REMINDER_DAYS,
    CONF_REMINDER_ENABLED,
    DEFAULT_REMINDER_DAYS,
    DOMAIN,
    MODE_VENTILATION,
    STATE_ON,
)
from .messages import text
from .util import async_notify

if TYPE_CHECKING:
    from .coordinator import EberspaecherDevice

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
CHECK_AT = time(10, 0)  # local time, once a day
REPEAT_AFTER = timedelta(days=7)
SAVE_DELAY = 30  # seconds


@dataclass(frozen=True)
class ReminderConfig:
    """The reminder options with defaults filled in."""

    enabled: bool = True
    days: int = DEFAULT_REMINDER_DAYS
    notify_service: str | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> ReminderConfig:
        try:
            days = int(options.get(CONF_REMINDER_DAYS, DEFAULT_REMINDER_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_REMINDER_DAYS
        return cls(
            enabled=bool(options.get(CONF_REMINDER_ENABLED, True)),
            days=days if days > 0 else DEFAULT_REMINDER_DAYS,
            notify_service=options.get(CONF_NOTIFY_SERVICE) or None,
        )


def _parse(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


class ReminderManager:
    """Remembers when the heater last heated and nags when it has been too long."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.reminder"
        )
        self.config = ReminderConfig()
        self.last_heated: datetime | None = None
        self._since: datetime | None = None  # when the integration first saw the heater
        self._last_reminded: datetime | None = None
        self._unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def _issue_id(self) -> str:
        return f"heater_idle_{self._entry_id}"

    async def async_load(self) -> None:
        """Read what was remembered; start counting from now when nothing was."""
        stored = await self._store.async_load() or {}
        self.last_heated = _parse(stored.get("last_heated"))
        self._since = _parse(stored.get("since"))
        self._last_reminded = _parse(stored.get("last_reminded"))
        if self._since is None:
            self._since = dt_util.utcnow()
            self._save()

    def _data(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "last_heated": iso(self.last_heated),
            "since": iso(self._since),
            "last_reminded": iso(self._last_reminded),
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def async_configure(self, options: dict[str, Any]) -> None:
        """(Re)start the daily check from the options."""
        self._cancel_timer()
        self.config = ReminderConfig.from_options(options)
        if self.config.enabled:
            self._unsub = async_track_time_change(
                self.hass,
                self._async_daily,
                hour=CHECK_AT.hour,
                minute=CHECK_AT.minute,
                second=0,
            )
            self.hass.async_create_task(self.async_check())
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

    def _cancel_timer(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def async_shutdown(self) -> None:
        self._cancel_timer()
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        await self._store.async_save(self._data())

    @callback
    def observe(self, devices: dict[str, EberspaecherDevice]) -> None:
        """Note a heater that the cloud reports running in heating mode."""
        for device in devices.values():
            for heater in device.heaters.values():
                if heater.get("state") != STATE_ON:
                    continue
                operation = heater.get("currentOperation") or {}
                mode = (operation.get("operationSettings") or {}).get("operationMode")
                if mode != MODE_VENTILATION:
                    self.mark_heated()
                    return

    @callback
    def mark_heated(self) -> None:
        """The heater is running or was just started in heating mode."""
        self.last_heated = dt_util.utcnow()
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._save()
        self._notify_listeners()

    def idle_days(self) -> int:
        reference = self.last_heated or self._since or dt_util.utcnow()
        return (dt_util.utcnow() - reference).days

    async def _async_daily(self, _now: datetime) -> None:
        await self.async_check()

    async def async_check(self) -> None:
        """Remind when the heater has been idle for too long (at most weekly)."""
        if not self.config.enabled:
            return
        days = self.idle_days()
        if days < self.config.days:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="heater_idle",
            translation_placeholders={"days": str(days)},
        )
        now = dt_util.utcnow()
        if self._last_reminded and now - self._last_reminded < REPEAT_AFTER:
            return
        self._last_reminded = now
        self._save()
        await async_notify(
            self.hass,
            self.config.notify_service,
            text(self.hass, "idle_title"),
            text(self.hass, "idle_body", days=days),
        )
