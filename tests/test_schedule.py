"""Ventilation, the temperature schedule, diagnostics and the repair issue."""
from __future__ import annotations

from datetime import datetime, time, timezone

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.eberspaecher.api import BASE_URL
from custom_components.eberspaecher.const import DOMAIN, FAILURES_BEFORE_ISSUE
from custom_components.eberspaecher.schedule import (
    Action,
    ScheduleConfig,
    choose_action,
    longest_runtime_fits,
    start_time,
    start_times,
)

from .conftest import EMAIL, IMEI, PASSWORD, calls_to, heater
from .test_integration import (
    SWITCH,
    open_options,
    set_heater_reply,
    setup,
    tick,
)

VENT_SWITCH = "switch.van_ventilation"
SCHEDULE_SWITCH = "switch.van_schedule"
SENSOR = "sensor.outside"

# Wednesday 4 Nov 2026
WED = datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc)
SAT = datetime(2026, 11, 7, 6, 30, tzinfo=timezone.utc)


# --- pure schedule logic ----------------------------------------------------

@pytest.mark.parametrize(
    ("temp", "expected"),
    [
        (15, None),
        (10.5, None),
        (10, Action("HEATING", 40)),
        (0.5, Action("HEATING", 40)),
        (0, Action("HEATING", 70)),
        (-9.5, Action("HEATING", 70)),
        (-10, Action("HEATING", 100)),
        (-25, Action("HEATING", 100)),
        (None, None),
    ],
)
def test_bands(temp, expected):
    assert choose_action(ScheduleConfig(), temp) == expected


def test_start_times_default():
    cfg = ScheduleConfig()
    assert start_times(cfg) == [time(6, 30), time(7, 0), time(7, 30)]
    assert start_time(cfg, 100) == time(6, 30)


def test_ventilation_when_hot():
    cfg = ScheduleConfig(vent_enabled=True)
    assert choose_action(cfg, 27) == Action("VENTILATION", 20)
    assert choose_action(cfg, 18) is None
    assert start_times(cfg)[-1] == time(7, 50)
    assert choose_action(ScheduleConfig(), 27) is None  # off by default


def test_runtime_must_fit_before_midnight():
    assert longest_runtime_fits(ScheduleConfig())
    assert not longest_runtime_fits(ScheduleConfig(ready_by=time(1, 0)))


def test_bad_options_fall_back_to_defaults():
    cfg = ScheduleConfig.from_options({"mild_runtime": "junk", "schedule_enabled": True})
    assert cfg == ScheduleConfig()


# --- ventilation ------------------------------------------------------------

async def test_ventilation_switch_starts_ventilation(hass, entry, cloud):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": VENT_SWITCH}, blocking=True)
    put = calls_to(cloud, "PUT", "/heaters/1")[-1][2]
    assert put["operationMode"] == "VENTILATION" and put["runtime"] == 30
    assert hass.states.get(VENT_SWITCH).state == "on"
    assert hass.states.get(SWITCH).state == "off"


async def test_switches_follow_the_running_mode(hass, entry, aioclient_mock):
    running = {"state": "ON", "heaterState": "ON", "currentOperation": {
        "remainingRuntime": 20, "operationSettings": {"operationMode": "VENTILATION"}}}
    set_heater_reply(aioclient_mock, heater(**running))
    await setup(hass, entry)
    assert hass.states.get(VENT_SWITCH).state == "on"
    assert hass.states.get(SWITCH).state == "off"
    running["currentOperation"]["operationSettings"]["operationMode"] = "HEATING"
    set_heater_reply(aioclient_mock, heater(**running))
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(VENT_SWITCH).state == "off"
    assert hass.states.get(SWITCH).state == "on"


async def test_no_ventilation_switch_without_the_capability(hass, entry, aioclient_mock):
    set_heater_reply(aioclient_mock, heater(capabilities=[{"operationMode": "HEATING"}]))
    await setup(hass, entry)
    assert hass.states.get(VENT_SWITCH) is None
    assert hass.states.get(SWITCH) is not None


async def test_ventilation_run_time_uses_its_own_limits(hass, entry, aioclient_mock):
    caps = [
        {"operationMode": "HEATING", "targetRuntime": {"minimumValue": 10, "maximumValue": 120}},
        {"operationMode": "VENTILATION", "targetRuntime": {"minimumValue": 5, "maximumValue": 20}},
    ]
    set_heater_reply(aioclient_mock, heater(capabilities=caps))
    await setup(hass, entry)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.van_run_time", "value": 90}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": VENT_SWITCH}, blocking=True)
    assert calls_to(aioclient_mock, "PUT", "/heaters/1")[-1][2]["runtime"] == 20  # clamped


# --- the schedule -----------------------------------------------------------

SCHEDULE_OPTIONS = {
    "schedule_enabled": True,
    "ready_by": "08:10:00",
    "weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "temperature_entity": SENSOR,
    "notify_service": "notify.phone",
}


@pytest.fixture
async def scheduled(hass, cloud):
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD},
                        options=dict(SCHEDULE_OPTIONS))
    e.add_to_hass(hass)
    notes = async_mock_service(hass, "notify", "phone")
    hass.states.async_set(SENSOR, "-12", {"unit_of_measurement": "°C"})
    await setup(hass, e)
    return e, notes


def at(hh, mm, base=WED):
    return base.replace(hour=hh, minute=mm)


async def test_very_cold_starts_at_0630_for_100_minutes(hass, scheduled, cloud):
    entry, notes = scheduled
    sched = entry.runtime_data.scheduler
    await sched.async_run(time(6, 30), at(6, 30))
    put = calls_to(cloud, "PUT", "/heaters/1")[-1][2]
    assert put["operationMode"] == "HEATING" and put["runtime"] == 100
    assert "started" in notes[-1].data["title"]
    assert "100 min, ready by 08:10" in notes[-1].data["message"]


async def test_wrong_wakeup_for_the_band_does_nothing(hass, scheduled, cloud):
    entry, notes = scheduled
    await entry.runtime_data.scheduler.async_run(time(7, 30), at(7, 30))  # -12 is the 06:30 band
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_only_one_start_per_day(hass, scheduled, cloud):
    entry, _ = scheduled
    sched = entry.runtime_data.scheduler
    await sched.async_run(time(6, 30), at(6, 30))
    hass.states.async_set(SENSOR, "-5")  # warmed up: now the 07:00 band
    await sched.async_run(time(7, 0), at(7, 0))
    assert len(calls_to(cloud, "PUT", "/heaters/1")) == 1
    hass.states.async_set(SENSOR, "-12")
    await sched.async_run(time(6, 30), at(6, 30, base=WED.replace(day=5)))  # next day runs again
    assert len(calls_to(cloud, "PUT", "/heaters/1")) == 2


async def test_weekend_is_skipped(hass, scheduled, cloud):
    entry, notes = scheduled
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30, base=SAT))
    assert calls_to(cloud, "PUT", "/heaters/1") == [] and notes == []


async def test_warm_day_says_not_needed_at_the_last_wakeup(hass, scheduled, cloud):
    entry, notes = scheduled
    hass.states.async_set(SENSOR, "14")
    sched = entry.runtime_data.scheduler
    await sched.async_run(time(6, 30), at(6, 30))
    assert notes == []                                   # not the last wake-up yet
    await sched.async_run(time(7, 30), at(7, 30))
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    assert "not needed" in notes[-1].data["title"]


async def test_summer_ventilation(hass, cloud):
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD},
                        options={**SCHEDULE_OPTIONS, "vent_enabled": True, "notify_service": ""})
    e.add_to_hass(hass)
    hass.states.async_set(SENSOR, "28")
    await setup(hass, e)
    await e.runtime_data.scheduler.async_run(time(7, 50), at(7, 50))
    put = calls_to(cloud, "PUT", "/heaters/1")[-1][2]
    assert put["operationMode"] == "VENTILATION" and put["runtime"] == 20


async def test_missing_sensor_falls_back_to_heater_reading(hass, scheduled, cloud):
    entry, _ = scheduled
    hass.states.async_set(SENSOR, "unavailable")
    set_heater_reply(cloud, heater(lastMeasuredTemperature={"temperature": -12}))
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 100


async def test_no_reading_does_not_start_and_says_so(hass, scheduled, cloud):
    entry, notes = scheduled
    hass.states.async_set(SENSOR, "unavailable")
    sched = entry.runtime_data.scheduler
    for t in (time(6, 30), time(7, 0), time(7, 30)):
        await sched.async_run(t, at(t.hour, t.minute))
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    assert len(notes) == 1 and "not started" in notes[0].data["title"]
    assert "Not started" in sched.last_result


async def test_running_heater_is_left_alone(hass, scheduled, cloud):
    entry, _ = scheduled
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_failed_start_notifies(hass, scheduled, cloud):
    entry, notes = scheduled
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    cloud.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater()])
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", status=500)
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert "failed" in notes[-1].data["title"]


async def test_schedule_switch_toggles_the_option(hass, scheduled):
    entry, _ = scheduled
    assert hass.states.get(SCHEDULE_SWITCH).state == "on"
    assert hass.states.get(SCHEDULE_SWITCH).attributes["start_times"] == ["06:30", "07:00", "07:30"]
    await hass.services.async_call("switch", "turn_off", {"entity_id": SCHEDULE_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["schedule_enabled"] is False
    assert hass.states.get(SCHEDULE_SWITCH).state == "off"
    assert entry.runtime_data.scheduler._unsubs == []


async def test_wakeups_are_registered_and_removed(hass, scheduled):
    entry, _ = scheduled
    scheduler = entry.runtime_data.scheduler
    assert len(scheduler._unsubs) == 3
    await hass.config_entries.async_unload(entry.entry_id)
    assert scheduler._unsubs == []


# --- settings on the device page --------------------------------------------

async def set_number(hass, entity_id, value):
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": entity_id, "value": value}, blocking=True)


async def test_settings_entities_show_the_defaults(hass, entry, cloud):
    await setup(hass, entry)
    assert hass.states.get("time.van_ready_by").state == "08:10:00"
    assert float(hass.states.get("number.van_no_heating_above").state) == 10
    assert float(hass.states.get("number.van_cold_at_or_below").state) == 0
    assert float(hass.states.get("number.van_very_cold_at_or_below").state) == -10
    assert float(hass.states.get("number.van_mild_run_time").state) == 40
    assert float(hass.states.get("number.van_very_cold_run_time").state) == 100
    assert hass.states.get("switch.van_summer_ventilation").state == "off"


async def test_settings_are_saved_in_the_options_and_applied(hass, entry, cloud):
    await setup(hass, entry)
    await set_number(hass, "number.van_very_cold_run_time", 90)
    await hass.services.async_call("time", "set_value",
                                   {"entity_id": "time.van_ready_by", "time": "07:45:00"}, blocking=True)
    await hass.services.async_call("switch", "turn_on",
                                   {"entity_id": "switch.van_summer_ventilation"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["very_cold_runtime"] == 90
    assert entry.options["ready_by"] == "07:45:00"
    cfg = entry.runtime_data.scheduler.config
    assert cfg.very_cold_runtime == 90 and cfg.vent_enabled
    assert hass.states.get("time.van_ready_by").state == "07:45:00"
    assert float(hass.states.get("number.van_very_cold_run_time").state) == 90


async def test_settings_changes_do_not_poll_the_cloud(hass, entry, cloud):
    await setup(hass, entry)
    reads = len(calls_to(cloud, "GET", "/heaters"))
    await set_number(hass, "number.van_mild_run_time", 35)
    await hass.async_block_till_done()
    assert len(calls_to(cloud, "GET", "/heaters")) == reads


@pytest.mark.parametrize(("entity", "value"), [
    ("number.van_cold_at_or_below", 12),         # above No heating above
    ("number.van_very_cold_at_or_below", 2),     # above Cold at or below
    ("number.van_very_cold_run_time", 600),      # starts before midnight
])
async def test_contradicting_settings_are_refused(hass, entry, cloud, entity, value):
    await setup(hass, entry)
    before = dict(entry.options)
    with pytest.raises(ServiceValidationError):
        await set_number(hass, entity, value)
    assert dict(entry.options) == before


async def test_ventilation_settings_hidden_without_the_capability(hass, entry, aioclient_mock):
    set_heater_reply(aioclient_mock, heater(capabilities=[{"operationMode": "HEATING"}]))
    await setup(hass, entry)
    assert hass.states.get("switch.van_summer_ventilation") is None
    assert hass.states.get("number.van_ventilate_at_or_above") is None
    assert hass.states.get("number.van_mild_run_time") is not None


# --- options flow -----------------------------------------------------------

async def test_schedule_options_saved(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "schedule")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "schedule_enabled": True, "ready_by": "07:45:00", "weekdays": ["mon", "tue"],
        "temperature_entity": SENSOR})
    assert r["type"] == "create_entry"
    assert entry.options["ready_by"] == "07:45:00"
    assert entry.options["notify_service"] is None
    assert entry.runtime_data.scheduler.config.weekdays == ("mon", "tue")


async def test_schedule_needs_a_day(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "schedule")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "schedule_enabled": True, "ready_by": "08:10:00", "weekdays": []})
    assert r["errors"] == {"base": "no_weekdays"}


async def test_bands_must_be_ordered(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "heating_bands")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "no_heat_above": 0, "mild_runtime": 40, "cold_below": 5, "cold_runtime": 70,
        "very_cold_below": -10, "very_cold_runtime": 100})
    assert r["errors"] == {"base": "bands_order"}


async def test_ventilation_must_start_above_heating(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "ventilation")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "vent_enabled": True, "vent_above": 8, "vent_runtime": 20})
    assert r["errors"] == {"base": "vent_below_heating"}


async def test_ventilation_options_saved_and_other_options_kept(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "polling")
    await hass.config_entries.options.async_configure(r["flow_id"], {"idle_interval": "30"})
    r = await open_options(hass, entry, "ventilation")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "vent_enabled": True, "vent_above": 26, "vent_runtime": 15})
    assert r["type"] == "create_entry"
    assert entry.options["idle_interval"] == 30
    assert entry.options["vent_above"] == 26.0 and entry.options["vent_enabled"] is True


# --- diagnostics and repair issue ------------------------------------------

async def test_diagnostics_redact_account_details(hass, entry, cloud):
    from homeassistant.components.diagnostics import REDACTED
    from custom_components.eberspaecher.diagnostics import async_get_config_entry_diagnostics
    await setup(hass, entry)
    data = await async_get_config_entry_diagnostics(hass, entry)
    text = str(data)
    assert EMAIL not in text and PASSWORD not in text and IMEI not in text
    assert data["entry"]["password"] == REDACTED
    assert "device_1" in data["devices"]


async def test_repair_issue_after_repeated_failures(hass, entry, cloud, freezer):
    await setup(hass, entry)
    registry = ir.async_get(hass)
    issue_id = f"cloud_unreachable_{entry.entry_id}"
    set_heater_reply(cloud, heater(), status=500)
    for _ in range(FAILURES_BEFORE_ISSUE - 1):
        await tick(hass, freezer, 601)
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    await tick(hass, freezer, 601)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    set_heater_reply(cloud, heater())
    await tick(hass, freezer, 601)
    assert registry.async_get_issue(DOMAIN, issue_id) is None
