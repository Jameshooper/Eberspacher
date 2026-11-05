"""Diagnostics for Eberspächer EasyStart Web."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import EberspaecherConfigEntry
from .const import (
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
    "imei",
    "token",
    "serialNumber",
    "name",
    "description",
}


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
        "devices": devices,
    }
