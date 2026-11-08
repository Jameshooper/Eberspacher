"""Diagnostics for Eberspächer EasyStart Web."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import EberspaecherConfigEntry
from .const import (
    CONF_SCHEDULE_DEVICE,
    CONF_LEARN_ENTITY,
    CONF_LEARN_TRACKER,
    CONF_NOTIFY_SERVICE,
    CONF_TEMPERATURE_ENTITY,
    CONF_WORK_TEMPERATURE_ENTITY,
)

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_NOTIFY_SERVICE,
    CONF_TEMPERATURE_ENTITY,
    CONF_WORK_TEMPERATURE_ENTITY,
    CONF_LEARN_ENTITY,
    CONF_LEARN_TRACKER,
    CONF_SCHEDULE_DEVICE,
    "imei",
    "token",
    "serialNumber",
    "name",
    "description",
    # Identifiers the cloud may add to a device or heater
    "iccid",
    "imsi",
    "msisdn",
    "phoneNumber",
    "ownerEmail",
}


_REDACT_KEYS = {key.casefold().replace("_", "") for key in TO_REDACT}
REDACTED = "**REDACTED**"


def async_redact_data(data: Any, to_redact: set[str] = TO_REDACT) -> Any:
    """Like Home Assistant's redaction, but a key matches in any spelling (IMEI, Imei,
    serial_number), since the cloud's field names are not documented."""
    keys = (
        _REDACT_KEYS
        if to_redact is TO_REDACT
        else {k.casefold().replace("_", "") for k in to_redact}
    )
    if isinstance(data, dict):
        return {
            k: (
                REDACTED
                if str(k).casefold().replace("_", "") in keys and v is not None
                else async_redact_data(v, to_redact)
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [async_redact_data(item, to_redact) for item in data]
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EberspaecherConfigEntry
) -> dict[str, Any]:
    """Return the setup and the latest cloud data with account details removed."""
    coordinator = entry.runtime_data
    # Devices are keyed by IMEI, so number them instead of printing the key.
    devices = {
        f"device_{index}": {"heaters": async_redact_data(device.heaters, TO_REDACT)}
        for index, device in enumerate((coordinator.data or {}).values(), start=1)
    }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "last_update_success": coordinator.last_update_success,
        "schedule_last_result": coordinator.scheduler.last_result,
        "work_schedule_last_result": coordinator.work_scheduler.last_result,
        "paused_until": (
            coordinator.paused_until.isoformat() if coordinator.paused_until else None
        ),
        "ready_once": {
            "ready_at": (
                coordinator.ready_once.ready_at.isoformat()
                if coordinator.ready_once.ready_at
                else None
            ),
            "last_result": coordinator.ready_once.last_result,
        },
        "car_check": {
            "last_checked": (
                coordinator.car_check.last_checked.isoformat()
                if coordinator.car_check.last_checked
                else None
            ),
            "last_ok": coordinator.car_check.last_ok,
        },
        "devices": devices,
    }
