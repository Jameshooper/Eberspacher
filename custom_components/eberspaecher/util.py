"""Small helpers shared by the platforms and the schedule."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


async def async_notify(
    hass: HomeAssistant, service: str | None, title: str, message: str
) -> None:
    """Send a message through a notify service such as notify.mobile_app_phone."""
    service = (service or "").strip()
    if not service:
        return
    domain, _, name = service.partition(".")
    if not name:
        domain, name = "notify", service
    if domain != "notify":
        # Only notifications: this setting must not become a way to run other actions.
        _LOGGER.warning("Not sending: %s is not a notify service", service)
        return
    if not hass.services.has_service(domain, name):
        _LOGGER.warning("Notify service %s.%s does not exist", domain, name)
        return
    try:
        await hass.services.async_call(
            domain, name, {"title": title, "message": message}, blocking=True
        )
    except HomeAssistantError as err:
        _LOGGER.warning("Could not send the notification: %s", err)


def parse_temperature(value: Any) -> float | None:
    """A temperature as a float from the API's reading or a state string, else None."""
    # The API returns a dict: {'temperature': 13, 'temperatureUnit': 'DEGREE_CELSIUS'}
    if isinstance(value, dict):
        value = value.get("temperature")
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
