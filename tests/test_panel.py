"""The sidebar control panel: what it is given, what it may call, and when it is shown."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.eberspaecher import panel
from custom_components.eberspaecher.const import DOMAIN

from .conftest import IMEI
from .test_integration import open_options, setup

WWW = Path(panel.__file__).parent / "www"
SCRIPT = (WWW / "eberspaecher-hud.js").read_text(encoding="utf-8")


@pytest.fixture
def frontend(hass):
    """Pretend the front end is loaded, and record what the panel asks of it."""
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.components.add("frontend")
    with (
        patch("homeassistant.components.panel_custom.async_register_panel", new=AsyncMock()) as add,
        patch("homeassistant.components.frontend.async_remove_panel") as remove,
    ):
        hass.data.setdefault("frontend_panels", {})
        yield add, remove


async def test_panel_is_added_with_the_entities_it_may_use(hass, entry, cloud, frontend):
    add, _ = frontend
    await setup(hass, entry)
    add.assert_awaited_once()
    kwargs = add.await_args.kwargs
    assert kwargs["frontend_url_path"] == "eberspaecher"
    assert kwargs["webcomponent_name"] == "eberspaecher-hud"
    assert kwargs["module_url"].startswith("/eberspaecher_static/eberspaecher-hud.js?v=")
    assert kwargs["require_admin"] is False
    registry = er.async_get(hass)
    entities = kwargs["config"]["entities"]
    assert entities["heating"] == registry.async_get_entity_id("switch", DOMAIN, f"{IMEI}_1_heating")
    assert entities["schedule"] == registry.async_get_entity_id("switch", DOMAIN, f"{IMEI}_schedule")
    assert entities["ready_once"].startswith("datetime.")
    assert entities["request_temperature"].startswith("button.")
    assert kwargs["config"]["car"] == {}
    hass.http.async_register_static_paths.assert_awaited_once()


async def test_panel_needs_the_front_end(hass, entry, cloud):
    """On a Home Assistant without the front end the integration still sets up."""
    await setup(hass, entry)
    assert entry.state.name == "LOADED"


async def test_car_sensors_are_passed_on_only_when_chosen_and_valid(hass, entry, cloud, frontend):
    add, _ = frontend
    hass.config_entries.async_update_entry(
        entry,
        options={
            "hud_ev": "sensor.car_battery",
            "hud_fuel": "switch.not_a_sensor",
            "hud_odometer": "sensor.car_odometer",
            "hud_ev_range": 5,
        },
    )
    await setup(hass, entry)
    assert add.await_args.kwargs["config"]["car"] == {
        "ev": "sensor.car_battery",
        "odometer": "sensor.car_odometer",
    }


async def test_panel_can_be_turned_off_and_back_on(hass, entry, cloud, frontend):
    add, remove = frontend
    await setup(hass, entry)
    assert add.await_count == 1
    hass.data["frontend_panels"]["eberspaecher"] = object()
    flow = await open_options(hass, entry, "hud")
    await hass.config_entries.options.async_configure(flow["flow_id"], {"hud_enabled": False})
    await hass.async_block_till_done()
    remove.assert_called_with(hass, "eberspaecher")
    assert add.await_count == 1
    flow = await open_options(hass, entry, "hud")
    await hass.config_entries.options.async_configure(flow["flow_id"], {"hud_enabled": True})
    await hass.async_block_till_done()
    assert add.await_count == 2


async def test_panel_options_are_saved_and_cleared(hass, entry, cloud):
    await setup(hass, entry)
    flow = await open_options(hass, entry, "hud")
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"hud_enabled": True, "hud_ev": "sensor.car_battery"}
    )
    assert entry.options["hud_ev"] == "sensor.car_battery"
    flow = await open_options(hass, entry, "hud")
    await hass.config_entries.options.async_configure(flow["flow_id"], {"hud_enabled": True})
    assert entry.options["hud_ev"] is None


async def test_panel_is_removed_when_the_integration_unloads(hass, entry, cloud, frontend):
    _, remove = frontend
    await setup(hass, entry)
    hass.data["frontend_panels"]["eberspaecher"] = object()
    await hass.config_entries.async_unload(entry.entry_id)
    remove.assert_called_with(hass, "eberspaecher")


# --- the page itself --------------------------------------------------------


def test_the_page_calls_only_its_own_kind_of_service():
    """The page may press, switch, set a number and set a date and time, nothing else."""
    calls = re.findall(r'call\("(\w+)", (.+?), \{', SCRIPT)
    assert calls
    assert {d for d, _ in calls} <= {"switch", "number", "button", "datetime"}
    services = {s for _, expr in calls for s in re.findall(r'"(\w+)"', expr)}
    assert services <= {"turn_on", "turn_off", "set_value", "press"}
    assert SCRIPT.count("callService(") == 1  # one place, so every call goes the same way


@pytest.mark.parametrize("word", ["fetch(", "XMLHttpRequest", "eval(", "localStorage", "document.cookie", "WebSocket", "sendBeacon"])
def test_the_page_makes_no_requests_of_its_own(word):
    assert word not in SCRIPT


def test_values_from_home_assistant_never_go_into_html():
    """State text reaches the page through textContent, never innerHTML."""
    assert [m for m in re.findall(r"innerHTML\s*=\s*([^;]+);", SCRIPT)] == [
        "`<style>${STYLE}</style>${HTML}`",
        "night ? NIGHT : DAY",
    ]


def test_the_page_files_exist():
    assert (WWW / "car.webp").is_file()
    assert "/eberspaecher_static/car.webp" in SCRIPT
    assert (WWW / "apple-touch-icon.png").is_file()
    assert "/eberspaecher_static/apple-touch-icon.png" in SCRIPT
