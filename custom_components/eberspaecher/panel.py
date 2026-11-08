"""The control panel in the Home Assistant sidebar.

A full-screen page for the car and its heater. It is a front-end panel that runs inside
Home Assistant's own page, so it uses the normal Home Assistant login: it can read the
states and call the services of the entities named below and nothing else, and no server,
port or password of its own is involved. On a phone, open the panel in Safari and use
Share, Add to Home Screen to get it as a web app.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from .const import (
    CONF_HUD_BATTERY_12V,
    CONF_HUD_ENABLED,
    CONF_HUD_EV,
    CONF_HUD_EV_RANGE,
    CONF_HUD_FUEL,
    CONF_HUD_FUEL_RANGE,
    CONF_HUD_ODOMETER,
    DOMAIN,
)

if TYPE_CHECKING:
    from .coordinator import EberspaecherCoordinator

_LOGGER = logging.getLogger(__name__)

PANEL_PATH = "eberspaecher"
COMPONENT = "eberspaecher-hud"
STATIC_URL = "/eberspaecher_static"
WWW = Path(__file__).parent / "www"
_STATIC_DONE = f"{DOMAIN}_static_path"

# panel name -> (entity domain, unique id key, is it per heater)
OWN_ENTITIES = {
    "heating": ("switch", "heating", True),
    "run_time": ("number", "run_time", True),
    "remaining": ("sensor", "remaining", True),
    "temperature": ("sensor", "temperature", True),
    "schedule": ("switch", "schedule", False),
    "learn": ("switch", "learn", False),
    "alt_on": ("switch", "alt_enabled", False),
    "ready_by": ("time", "ready_by", False),
    "alt_ready_by": ("time", "alt_ready_by", False),
    "last_heated": ("sensor", "last_heated", False),
    "ready_once": ("datetime", "ready_once", False),
    "cancel_ready_once": ("button", "cancel_ready_once", False),
    "request_temperature": ("button", "request_temperature", False),
    "pause_until": ("date", "pause_until", False),
}
# panel name -> option holding the entity chosen in the options
CAR_ENTITIES = {
    "ev": CONF_HUD_EV,
    "fuel": CONF_HUD_FUEL,
    "ev_range": CONF_HUD_EV_RANGE,
    "fuel_range": CONF_HUD_FUEL_RANGE,
    "odometer": CONF_HUD_ODOMETER,
    "battery_12v": CONF_HUD_BATTERY_12V,
}


def panel_config(hass: HomeAssistant, coordinator: EberspaecherCoordinator) -> dict[str, Any] | None:
    """The entities the panel may use, found by their unique ids. None if there is no device."""
    imei = coordinator.primary_imei
    if imei is None:
        return None
    heaters = sorted((coordinator.data or {}).get(imei).heaters) if coordinator.data else []
    if not heaters:
        return None
    address = heaters[0]
    registry = er.async_get(hass)
    own: dict[str, str] = {}
    for name, (domain, key, per_heater) in OWN_ENTITIES.items():
        unique_id = f"{imei}_{address}_{key}" if per_heater else f"{imei}_{key}"
        if entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id):
            own[name] = entity_id
    options = coordinator.config_entry.options
    car: dict[str, str] = {}
    for name, option in CAR_ENTITIES.items():
        value = options.get(option)
        if isinstance(value, str) and value.count(".") == 1 and value.split(".")[0] == "sensor":
            car[name] = value
    return {"entities": own, "car": car}


def _available(hass: HomeAssistant) -> bool:
    """Whether the front end can show a panel (it is absent on a bare Home Assistant)."""
    return getattr(hass, "http", None) is not None and "frontend" in hass.config.components


async def async_register_panel(hass: HomeAssistant, coordinator: EberspaecherCoordinator) -> None:
    """Add the sidebar panel, or take it away when it is turned off in the options."""
    async_remove_panel(hass)
    if not coordinator.config_entry.options.get(CONF_HUD_ENABLED, True) or not _available(hass):
        return
    config = panel_config(hass, coordinator)
    if config is None:
        return
    from homeassistant.components import panel_custom

    if not hass.data.get(_STATIC_DONE):
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(WWW), False)])
        hass.data[_STATIC_DONE] = True
    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=COMPONENT,
        frontend_url_path=PANEL_PATH,
        module_url=f"{STATIC_URL}/eberspaecher-hud.js?v={version}",
        sidebar_title="Optima",
        sidebar_icon="mdi:car-electric",
        require_admin=False,
        config=config,
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    """Take the sidebar panel away (nothing happens if it is not there)."""
    if not _available(hass):
        return
    from homeassistant.components import frontend

    if PANEL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_PATH)
