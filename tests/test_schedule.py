"""Ventilation, the temperature schedule, diagnostics and the repair issue."""
from __future__ import annotations

from datetime import datetime, time, timezone

import pytest
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


# --- pausing ----------------------------------------------------------------

PAUSE = "date.van_pause_until"


async def test_pause_until_skips_those_days_and_resumes_after(hass, scheduled, cloud):
    entry, _ = scheduled
    sched = entry.runtime_data.scheduler
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": WED.date().isoformat()},
                                   blocking=True)
    assert hass.states.get(PAUSE).state == WED.date().isoformat()
    await sched.async_run(time(6, 30), at(6, 30))        # the last paused day
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    assert sched.last_result == f"Paused until {WED.date().isoformat()}"
    await sched.async_run(time(6, 30), at(6, 30, base=WED.replace(day=WED.day + 1)))
    assert len(calls_to(cloud, "PUT", "/heaters/1")) == 1  # the day after runs


async def test_pause_is_restored_after_a_restart(hass, scheduled, cloud):
    entry, _ = scheduled
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": "2030-01-02"}, blocking=True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.paused_until.isoformat() == "2030-01-02"


async def test_changing_the_pause_updates_the_last_result(hass, scheduled, cloud):
    entry, _ = scheduled
    sched = entry.runtime_data.scheduler
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": WED.date().isoformat()},
                                   blocking=True)
    await sched.async_run(time(6, 30), at(6, 30))
    assert sched.last_result.startswith("Paused")
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": "2026-01-01"}, blocking=True)
    assert sched.last_result is None
    assert hass.states.get("switch.van_schedule").attributes["last_result"] is None


# --- a second group of days with its own time --------------------------------

TIME_FIRST = "time.van_ready_by_first_days"
TIME_SECOND = "time.van_ready_by_second_days"
LEARN_SWITCH = "switch.van_learn_times"


def test_second_group_of_days_has_its_own_time():
    cfg = ScheduleConfig.from_options({**SCHEDULE_OPTIONS, "alt_ready_by": "10:00:00", "alt_weekdays": ["sat", "sun"]})
    assert cfg.alt_ready_by == time(10, 0) and cfg.alt_weekdays == ("sat", "sun")
    assert ScheduleConfig.from_options(SCHEDULE_OPTIONS).alt_ready_by is None


def test_second_time_must_also_fit_the_longest_run():
    cfg = ScheduleConfig(alt_ready_by=time(1, 0))  # 100 min do not fit before 01:00
    assert not longest_runtime_fits(cfg)


async def test_weekend_uses_its_own_ready_by_time(hass, cloud):
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data={"email": EMAIL, "password": PASSWORD},
                        options={**SCHEDULE_OPTIONS, "alt_ready_by": "10:00:00", "alt_weekdays": ["sat", "sun"]})
    e.add_to_hass(hass)
    async_mock_service(hass, "notify", "phone")
    hass.states.async_set(SENSOR, "-12", {"unit_of_measurement": "°C"})
    await setup(hass, e)
    sched = e.runtime_data.scheduler
    assert sched.config_for(SAT.date()).ready_by == time(10, 0)
    assert sched.config_for(WED.date()).ready_by == time(8, 10)
    # Saturday: -12 °C needs 100 min, so the start is 08:20, not skipped.
    await sched.async_run(time(8, 20), at(8, 20, base=SAT))
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 100


async def test_weekend_stays_off_without_a_second_time(hass, scheduled, cloud):
    entry, _ = scheduled
    assert hass.states.get(TIME_SECOND).state == "unknown"
    assert entry.runtime_data.scheduler.config_for(SAT.date()).weekdays == ("mon", "tue", "wed", "thu", "fri")


async def test_the_time_entities_save_to_the_options(hass, scheduled):
    entry, _ = scheduled
    assert hass.states.get(TIME_FIRST).state == "08:10:00"
    await hass.services.async_call("time", "set_value", {"entity_id": TIME_SECOND, "time": "09:30:00"}, blocking=True)
    await hass.services.async_call("time", "set_value", {"entity_id": TIME_FIRST, "time": "07:45:00"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["alt_ready_by"] == "09:30:00" and entry.options["ready_by"] == "07:45:00"
    assert hass.states.get(TIME_SECOND).attributes["weekdays"] == ["sat", "sun"]


async def test_a_time_too_early_for_the_longest_run_is_refused(hass, scheduled):
    from homeassistant.exceptions import ServiceValidationError
    entry, _ = scheduled
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("time", "set_value", {"entity_id": TIME_FIRST, "time": "01:00:00"}, blocking=True)
    assert entry.options["ready_by"] == "08:10:00"


async def test_learn_switch_needs_a_signal_first(hass, scheduled):
    from homeassistant.exceptions import ServiceValidationError
    entry, _ = scheduled
    assert hass.states.get(LEARN_SWITCH).state == "off"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("switch", "turn_on", {"entity_id": LEARN_SWITCH}, blocking=True)
    assert not entry.options.get("learn_enabled")


async def test_learn_switch_toggles_the_option(hass, scheduled):
    entry, _ = scheduled
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "learn_entity": "sensor.car", "learn_tracker": "person.me"})
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": LEARN_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["learn_enabled"] is True and hass.states.get(LEARN_SWITCH).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": LEARN_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["learn_enabled"] is False


async def test_second_time_saved_and_cleared_in_the_options_flow(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "schedule")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "schedule_enabled": True, "ready_by": "07:45:00", "weekdays": ["mon", "tue"],
        "alt_ready_by": "09:00:00", "alt_weekdays": ["sat"]})
    assert entry.options["alt_ready_by"] == "09:00:00" and entry.options["alt_weekdays"] == ["sat"]
    r = await open_options(hass, entry, "schedule")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "schedule_enabled": True, "ready_by": "07:45:00", "weekdays": ["mon", "tue"]})
    assert entry.options["alt_ready_by"] is None


ALT_SWITCH = "switch.van_second_days_on"


async def test_second_days_can_be_turned_off_and_on_keeping_the_time(hass, cloud):
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data={"email": EMAIL, "password": PASSWORD},
                        options={**SCHEDULE_OPTIONS, "alt_ready_by": "10:00:00", "alt_weekdays": ["sat", "sun"]})
    e.add_to_hass(hass)
    async_mock_service(hass, "notify", "phone")
    hass.states.async_set(SENSOR, "-12", {"unit_of_measurement": "°C"})
    await setup(hass, e)
    sched = e.runtime_data.scheduler
    assert hass.states.get(ALT_SWITCH).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": ALT_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(ALT_SWITCH).state == "off"
    assert hass.states.get(TIME_SECOND).state == "10:00:00"        # the time is kept
    await sched.async_run(time(8, 20), at(8, 20, base=SAT))        # Saturday is skipped
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    await hass.services.async_call("switch", "turn_on", {"entity_id": ALT_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert e.options["alt_ready_by"] == "10:00:00" and sched.config_for(SAT.date()).ready_by == time(10, 0)


async def test_turning_second_days_on_without_a_time_uses_the_first_time(hass, scheduled):
    entry, _ = scheduled
    assert hass.states.get(ALT_SWITCH).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": ALT_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["alt_ready_by"] == "08:10:00" and hass.states.get(ALT_SWITCH).state == "on"


async def test_setting_the_second_time_turns_those_days_on(hass, scheduled):
    entry, _ = scheduled
    hass.config_entries.async_update_entry(entry, options={**entry.options, "alt_ready_by": "10:00:00", "alt_enabled": False})
    await hass.async_block_till_done()
    await hass.services.async_call("time", "set_value", {"entity_id": TIME_SECOND, "time": "09:00:00"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["alt_enabled"] is True and hass.states.get(ALT_SWITCH).state == "on"


# --- alternating weeks, learned-only days, messages and the next start ---------

from datetime import date, timedelta  # noqa: E402

from custom_components.eberspaecher.learning import week_parity  # noqa: E402

SAT_A = date(2026, 11, 7)    # a Saturday, and the Saturdays two weeks apart from it
SAT_B = date(2026, 11, 14)


def put_samples(learner, days, minutes, kind="home"):
    for d in days:
        learner._samples[kind][d.isoformat()] = minutes


async def learning_entry(hass, cloud, **extra):
    await hass.config.async_set_time_zone("UTC")
    options = {**SCHEDULE_OPTIONS, "alt_ready_by": "10:00:00", "alt_weekdays": ["sat", "sun"],
               "learn_enabled": True, "learn_entity": "sensor.audio", "learn_tracker": "person.me", **extra}
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data={"email": EMAIL, "password": PASSWORD}, options=options)
    e.add_to_hass(hass)
    notes = async_mock_service(hass, "notify", "phone")
    hass.states.async_set(SENSOR, "-12", {"unit_of_measurement": "°C"})
    await setup(hass, e)
    return e, notes


def saturdays_like(day, count):
    """Saturdays before `day` in the same kind of week (two weeks apart)."""
    return [day - timedelta(weeks=2 * (i + 1)) for i in range(count)]


async def test_alternating_weeks_are_learned_apart(hass, cloud):
    e, _ = await learning_entry(hass, cloud, learn_alternate=True)
    learner = e.runtime_data.learner
    assert week_parity(SAT_A) != week_parity(SAT_B)
    put_samples(learner, saturdays_like(SAT_A, 3), 8 * 60)    # the weekends she works
    put_samples(learner, saturdays_like(SAT_B, 3), 10 * 60)   # the weekends she does not
    assert learner.learned_minutes("home", 5, SAT_A) == 8 * 60 - 5
    assert learner.learned_minutes("home", 5, SAT_B) == 10 * 60 - 5


async def test_without_alternating_weeks_they_are_mixed(hass, cloud):
    e, _ = await learning_entry(hass, cloud)
    learner = e.runtime_data.learner
    put_samples(learner, saturdays_like(SAT_A, 3), 8 * 60)
    put_samples(learner, saturdays_like(SAT_B, 3), 10 * 60)
    assert learner.learned_minutes("home", 5, SAT_B) == 8 * 60 - 5   # the early weeks pull it


async def test_learned_only_skips_weekends_without_a_learned_time(hass, cloud):
    e, _ = await learning_entry(hass, cloud, learn_alternate=True, alt_learned_only=True)
    sched = e.runtime_data.scheduler
    put_samples(e.runtime_data.learner, saturdays_like(SAT_A, 3), 8 * 60)
    assert "sat" in sched.config_for(SAT_A).weekdays       # a weekend she works: it runs
    assert "sat" not in sched.config_for(SAT_B).weekdays   # the other weekend: nothing learned, skipped


async def test_without_learned_only_a_weekend_uses_its_fixed_time(hass, cloud):
    e, _ = await learning_entry(hass, cloud, learn_alternate=True)
    assert "sat" in e.runtime_data.scheduler.config_for(SAT_B).weekdays
    assert e.runtime_data.scheduler.config_for(SAT_B).ready_by == time(10, 0)


async def test_a_new_learned_time_sends_a_message(hass, cloud):
    e, notes = await learning_entry(hass, cloud)
    learner = e.runtime_data.learner
    put_samples(learner, [date(2026, 10, 14), date(2026, 10, 21)], 7 * 60 + 40)
    learner.record(datetime(2026, 10, 28, 7, 40, tzinfo=timezone.utc), "home")   # the third Wednesday
    await hass.async_block_till_done()
    assert notes[-1].data["title"] == "Learned a new time"
    assert "Wednesdays" in notes[-1].data["message"] and "07:35" in notes[-1].data["message"]


async def test_a_barely_changed_time_sends_no_message(hass, cloud):
    e, notes = await learning_entry(hass, cloud)
    learner = e.runtime_data.learner
    put_samples(learner, [date(2026, 10, 14), date(2026, 10, 21), date(2026, 10, 7)], 7 * 60 + 40)
    before = len(notes)
    learner.record(datetime(2026, 10, 28, 7, 42, tzinfo=timezone.utc), "home")   # 2 min different
    await hass.async_block_till_done()
    assert len(notes) == before


async def test_next_plan_names_the_next_day_and_time(hass, cloud, freezer):
    freezer.move_to("2026-11-04 05:00:00+00:00")    # Wednesday, before the ready-by time
    e, _ = await learning_entry(hass, cloud)
    sched = e.runtime_data.scheduler
    assert sched.next_plan() == (date(2026, 11, 4), time(8, 10), False)
    freezer.move_to("2026-11-04 09:00:00+00:00")    # past it: tomorrow
    assert sched.next_plan()[0] == date(2026, 11, 5)
    freezer.move_to("2026-11-06 18:00:00+00:00")    # Friday evening: the weekend time is next
    assert sched.next_plan() == (date(2026, 11, 7), time(10, 0), False)


async def test_next_plan_skips_a_day_already_started_and_a_paused_day(hass, cloud, freezer):
    freezer.move_to("2026-11-04 05:00:00+00:00")
    e, _ = await learning_entry(hass, cloud)
    sched = e.runtime_data.scheduler
    e.runtime_data.started_on["home"] = "2026-11-04"
    assert sched.next_plan()[0] == date(2026, 11, 5)
    assert hass.states.get(SCHEDULE_SWITCH).attributes["next_day"] in ("2026-11-04", "2026-11-05")


async def test_next_plan_is_empty_when_the_schedule_is_off(hass, scheduled):
    entry, _ = scheduled
    await hass.services.async_call("switch", "turn_off", {"entity_id": SCHEDULE_SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data.scheduler.next_plan() is None
    assert hass.states.get(SCHEDULE_SWITCH).attributes["next_day"] is None
