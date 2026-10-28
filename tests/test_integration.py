"""End-to-end tests inside a real Home Assistant core: setup, entities, switch, service."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from homeassistant.core import State

from custom_components.eberspaecher.api import BASE_URL
from custom_components.eberspaecher.const import DOMAIN

from .conftest import IMEI, calls_to, heater

SWITCH = "switch.van_heating"
RUNTIME = "number.van_run_time"
REMAINING = "sensor.van_remaining_run_time"
TEMP = "sensor.van_temperature"
BUTTON = "button.van_request_temperature"


async def setup(hass, entry):
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def set_heater_reply(mock, *heaters, status=200):
    mock.clear_requests()
    mock.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    mock.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=list(heaters), status=status)
    mock.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    mock.post(f"{BASE_URL}/calls/{IMEI}/temperature", text="")


# --- setup / entities -------------------------------------------------------

async def test_setup_creates_entities(hass, entry, cloud):
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(RUNTIME).state == "30"
    assert hass.states.get(RUNTIME).attributes["min"] == 10
    assert hass.states.get(RUNTIME).attributes["max"] == 120
    assert hass.states.get(REMAINING).state == "0"
    assert hass.states.get(TEMP).state == "unknown"
    assert hass.states.get(BUTTON) is not None
    reg = er.async_get(hass)
    status = reg.async_get("sensor.van_status")
    assert status is not None and status.disabled_by is not None


async def test_unique_ids_are_stable_and_unique(hass, entry, cloud):
    await setup(hass, entry)
    ids = [e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)]
    assert len(ids) == len(set(ids)) == 6


async def test_auth_failure_at_setup_starts_reauth(hass, entry, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=401)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_cloud_down_at_setup_retries(hass, entry, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=503)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize("bad", [{"address": None}, {"address": "front"}])
async def test_malformed_heater_address_is_skipped(hass, entry, aioclient_mock, bad, caplog):
    """A heater with an unusable address is skipped and logged; the rest still loads."""
    good = heater(address=2)
    set_heater_reply(aioclient_mock, heater(**bad), good)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(SWITCH) is not None  # heater 2
    assert len(hass.states.async_entity_ids("switch")) == 1
    assert "unusable address" in caplog.text
    assert IMEI not in caplog.text  # only the last digits are logged


@pytest.mark.parametrize("caps", [
    [{"operationMode": "HEATING", "targetRuntime": {"minimumValue": "ten"}}],
    [{"operationMode": "HEATING", "targetRuntime": {"minimumValue": 200, "maximumValue": 50}}],
    [{"operationMode": "HEATING", "targetRuntime": "junk"}, "junk"],
    "junk",
])
async def test_malformed_capabilities_fall_back_to_defaults(hass, entry, aioclient_mock, caps):
    set_heater_reply(aioclient_mock, heater(capabilities=caps))
    await setup(hass, entry)
    state = hass.states.get(RUNTIME)
    assert state.attributes["min"] == 10 and state.attributes["max"] == 120
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert calls_to(aioclient_mock, "PUT", "/heaters/1")[-1][2]["runtime"] == 30


@pytest.mark.parametrize(("raw", "shown"), [
    ("21.5", "21.5"), (-7, "-7.0"), ("n/a", "unknown"), (True, "unknown"),
])
async def test_temperature_parsing(hass, entry, aioclient_mock, raw, shown):
    set_heater_reply(aioclient_mock, heater(lastMeasuredTemperature=raw))
    await setup(hass, entry)
    assert hass.states.get(TEMP).state == shown


async def test_running_heater_state_and_faster_poll(hass, entry, aioclient_mock):
    set_heater_reply(aioclient_mock, heater(state="ON", heaterState="ON",
                                            currentOperation={"remainingRuntime": 17}))
    await setup(hass, entry)
    assert hass.states.get(SWITCH).state == "on"
    assert hass.states.get(REMAINING).state == "17"
    assert entry.runtime_data.update_interval == timedelta(minutes=5)


async def test_device_vanishes_entities_unavailable(hass, entry, cloud, freezer):
    await setup(hass, entry)
    set_heater_reply(cloud)  # no heaters any more
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "unavailable"


# --- switch / commands ------------------------------------------------------

async def test_turn_on_uses_run_time_number(hass, entry, cloud):
    await setup(hass, entry)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": RUNTIME, "value": 45}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    put = calls_to(cloud, "PUT", "/heaters/1")[-1]
    assert put[2] == {**put[2], "operationMode": "HEATING", "runtime": 45}
    assert hass.states.get(SWITCH).state == "on"  # optimistic


async def test_turn_off(hass, entry, cloud):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    put = calls_to(cloud, "PUT", "/heaters/1")[-1]
    assert put[2]["operationMode"] == "OFF" and put[2]["runtime"] is None


async def test_number_rejects_out_of_range(hass, entry, cloud):
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("number", "set_value",
                                       {"entity_id": RUNTIME, "value": 500}, blocking=True)


@pytest.mark.parametrize("runtime", [5, 121, 600])
async def test_service_runtime_outside_heater_limits_rejected(hass, entry, cloud, runtime):
    """start_heating must respect the heater's own 10-120 min limits."""
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_heating",
                                       {"entity_id": SWITCH, "runtime": runtime}, blocking=True)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_service_runtime_within_limits_and_ventilation(hass, entry, cloud):
    await setup(hass, entry)
    await hass.services.async_call(DOMAIN, "start_heating",
                                   {"entity_id": SWITCH, "runtime": 120, "mode": "VENTILATION"},
                                   blocking=True)
    put = calls_to(cloud, "PUT", "/heaters/1")[-1][2]
    assert put["runtime"] == 120 and put["operationMode"] == "VENTILATION"


async def test_service_rejects_unsupported_mode(hass, entry, aioclient_mock):
    h = heater(capabilities=[{"operationMode": "HEATING",
                              "targetRuntime": {"minimumValue": 10, "maximumValue": 120}}])
    set_heater_reply(aioclient_mock, h)
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_heating",
                                       {"entity_id": SWITCH, "mode": "VENTILATION"}, blocking=True)
    assert calls_to(aioclient_mock, "PUT", "/heaters/1") == []


async def test_service_rejects_bad_input(hass, entry, cloud):
    await setup(hass, entry)
    for data in ({"runtime": 0}, {"runtime": 601}, {"mode": "OFF"}, {"runtime": "abc"}):
        with pytest.raises(Exception):
            await hass.services.async_call(DOMAIN, "start_heating",
                                           {"entity_id": SWITCH, **data}, blocking=True)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_command_failure_surfaces_and_keeps_state(hass, entry, cloud):
    await setup(hass, entry)
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", status=500)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert hass.states.get(SWITCH).state == "off"


async def test_refresh_after_command(hass, entry, cloud, freezer):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    before = len(calls_to(cloud, "GET", "/heaters"))
    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls_to(cloud, "GET", "/heaters")) == before + 1


async def test_unconfirmed_start_reverts_after_pending_window(hass, entry, cloud, freezer):
    """If the cloud never confirms, the switch returns to the real state after 90 s."""
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    # 15 s re-read: cloud still says OFF; pending keeps 'on' (expected, within 90 s).
    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "on"
    reads = len(calls_to(cloud, "GET", "/heaters"))
    # Past the 90 s pending window, well before the 10-minute poll.
    freezer.tick(timedelta(seconds=80))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"
    assert len(calls_to(cloud, "GET", "/heaters")) == reads + 1  # re-read on expiry


async def test_confirmed_start_stays_on(hass, entry, cloud, freezer):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "on"


async def test_request_temperature_button(hass, entry, cloud):
    await setup(hass, entry)
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    assert len(calls_to(cloud, "POST", "/temperature")) == 1


async def test_run_time_restored(hass, entry, cloud):
    mock_restore_cache_with_extra_data(hass, [(
        State(RUNTIME, "55"),
        {"native_value": 55, "native_min_value": 10, "native_max_value": 120,
         "native_step": 1, "native_unit_of_measurement": "min"},
    )])
    await setup(hass, entry)
    assert hass.states.get(RUNTIME).state == "55"
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 55


async def test_unload_cancels_pending_refresh(hass, entry, cloud, freezer):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    before = len(cloud.mock_calls)
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(cloud.mock_calls) == before
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_reload(hass, entry, cloud):
    await setup(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


# --- settling re-reads while the cloud reports a transitional state --------

def heater_reads(mock):
    return len(calls_to(mock, "GET", "/heaters"))


async def tick(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_transitional_state_is_re_read_until_settled(hass, entry, cloud, freezer):
    await setup(hass, entry)
    set_heater_reply(cloud, heater(state="ON", heaterState="ACTIVATION_REQUESTED"))
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    await tick(hass, freezer, 16)            # post-command read -> still requested
    assert heater_reads(cloud) == 1
    await tick(hass, freezer, 46)            # follow-up read
    assert heater_reads(cloud) == 2
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await tick(hass, freezer, 46)            # settles
    assert heater_reads(cloud) == 1          # (counter reset by set_heater_reply)
    await tick(hass, freezer, 46)            # no further follow-ups once settled
    assert heater_reads(cloud) == 1
    assert hass.states.get(SWITCH).state == "on"


async def test_transitional_re_reads_are_capped(hass, entry, aioclient_mock, freezer):
    set_heater_reply(aioclient_mock, heater(heaterState="ACTIVATION_REQUESTED"))
    await setup(hass, entry)
    start = heater_reads(aioclient_mock)
    for _ in range(10):
        await tick(hass, freezer, 46)
    # 6 follow-ups max; the next read is the regular 5-minute poll, not before.
    assert heater_reads(aioclient_mock) - start == 6
    await tick(hass, freezer, 300)
    assert heater_reads(aioclient_mock) - start == 7


async def test_new_command_resets_the_cap(hass, entry, aioclient_mock, freezer):
    set_heater_reply(aioclient_mock, heater(heaterState="DEACTIVATION_REQUESTED"))
    await setup(hass, entry)
    for _ in range(8):
        await tick(hass, freezer, 46)
    assert entry.runtime_data.settle_reads == 6
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    assert entry.runtime_data.settle_reads == 0
    before = heater_reads(aioclient_mock)
    await tick(hass, freezer, 16)
    await tick(hass, freezer, 46)
    assert heater_reads(aioclient_mock) - before == 2


async def test_idle_heater_has_no_follow_up_reads(hass, entry, cloud, freezer):
    await setup(hass, entry)
    start = heater_reads(cloud)
    await tick(hass, freezer, 46 * 3)
    assert heater_reads(cloud) == start
    assert entry.runtime_data.update_interval == timedelta(minutes=10)
