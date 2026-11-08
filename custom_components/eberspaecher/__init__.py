"""The Eberspächer EasyStart Web integration (unofficial)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import EberspaecherApi
from .const import DOMAIN
from .coordinator import EberspaecherCoordinator, idle_interval
from .panel import async_register_panel, async_remove_panel

PLATFORMS: list[Platform] = [
    Platform.DATE,
    Platform.DATETIME,
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
    await coordinator.reminder.async_load()
    await coordinator.learner.async_load()
    await coordinator.async_load_started()
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_pin_schedule_device()

    entry.runtime_data = coordinator
    _configure(coordinator, entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass, coordinator)
    return True


def _configure(coordinator: EberspaecherCoordinator, entry: EberspaecherConfigEntry) -> None:
    """Hand the options to the parts that act on them (the learner comes first)."""
    options = dict(entry.options)
    coordinator.learner.async_configure(options)
    coordinator.scheduler.async_configure(options)
    coordinator.work_scheduler.async_configure(options)
    coordinator.reminder.async_configure(options)
    coordinator.car_check.async_configure(options)
    coordinator.ready_once.async_replan()


async def _async_options_updated(hass: HomeAssistant, entry: EberspaecherConfigEntry) -> None:
    """Apply changed options without a reload."""
    coordinator = entry.runtime_data
    _configure(coordinator, entry)
    await async_register_panel(hass, coordinator)
    interval = idle_interval(entry)
    if interval != coordinator.idle_interval:
        coordinator.idle_interval = interval
        await coordinator.async_request_refresh()


async def async_remove_entry(hass: HomeAssistant, entry: EberspaecherConfigEntry) -> None:
    """Delete what the integration kept about you, such as the learned departures."""
    for name in ("departures", "reminder", "schedule"):
        await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{name}").async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: EberspaecherConfigEntry) -> bool:
    """Unload a config entry."""
    async_remove_panel(hass)
    entry.runtime_data.scheduler.async_stop()
    entry.runtime_data.work_scheduler.async_stop()
    await entry.runtime_data.learner.async_shutdown()
    await entry.runtime_data.reminder.async_shutdown()
    entry.runtime_data.car_check.async_stop()
    entry.runtime_data.ready_once.async_stop()
    await entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
