"""The Eberspächer EasyStart Web integration (unofficial)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EberspaecherApi
from .coordinator import EberspaecherCoordinator

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BUTTON,
]

EberspaecherConfigEntry = ConfigEntry[EberspaecherCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EberspaecherConfigEntry) -> bool:
    """Set up a config entry."""
    api = EberspaecherApi(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    coordinator = EberspaecherCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EberspaecherConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
