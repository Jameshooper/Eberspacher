"""Learning when you leave, and the schedule for leaving work."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.eberspaecher import schedule as schedule_module
from custom_components.eberspaecher.const import DOMAIN
from custom_components.eberspaecher.learning import (
    classify,
    samples_from_history,
)
from custom_components.eberspaecher.schedule import ScheduleConfig, start_times

from .conftest import EMAIL, PASSWORD, calls_to, heater
from .test_integration import open_options, set_heater_reply, setup

SIGNAL = "sensor.phone_audio_output"
TRACKER = "device_tracker.phone"
OUTSIDE = "sensor.outside"
UTC = timezone.utc

# Wednesday 4 Nov 2026 and the Wednesdays before it
WED = datetime(2026, 11, 4, 0, 0, tzinfo=UTC)


def wednesdays(count):
    return [(WED - timedelta(weeks=i + 1)).date() for i in range(count)]


# --- pure logic ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("hour", "place", "expected"),
    [
        (8, "home", "home"),
        (8, "not_home", None),      # a morning connection away from home is not leaving home
        (16, "not_home", "work"),
        (16, "Work", "work"),       # a named zone counts as away too
        (16, "home", None),         # an evening errand from home is not leaving home
        (12, "not_home", None),     # a lunch trip
        (3, "home", None),
        (16, None, None),
    ],
)
def test_classify(hour, place, expected):
    local = datetime(2026, 11, 4, hour, 5, tzinfo=UTC)
    assert classify(local, place) == expected


def test_a_phone_that_just_left_home_still_counts_as_leaving_home():
    local = datetime(2026, 11, 4, 8, 5, tzinfo=UTC)
    assert classify(local, "not_home", recently_home=True) == "home"
    assert classify(datetime(2026, 11, 4, 14, 5, tzinfo=UTC), "not_home", recently_home=True) is None


def test_history_counts_a_phone_that_left_home_minutes_before_connecting():
    def t(hh, mm):
        return datetime(2026, 11, 3, hh, mm, tzinfo=UTC)

    signal = [(t(7, 0), "Built-in Speaker"), (t(8, 5), "CarPlay"), (t(9, 0), "Built-in Speaker")]
    tracker = [(t(0, 0), "home"), (t(8, 0), "not_home")]   # left five minutes earlier
    assert samples_from_history(signal, tracker, "carplay", UTC)["home"] == {"2026-11-03": 8 * 60 + 5}
    tracker = [(t(0, 0), "home"), (t(7, 10), "not_home")]  # long gone: not leaving home
    assert samples_from_history(signal, tracker, "carplay", UTC)["home"] == {}


def test_history_gives_the_first_connection_of_each_morning_and_afternoon():
    def t(day, hh, mm):
        return datetime(2026, 11, day, hh, mm, tzinfo=UTC)

    signal = [
        (t(2, 7, 0), "Built-in Speaker"),
        (t(2, 8, 5), "CarPlay"),
        (t(2, 8, 30), "Built-in Speaker"),
        (t(2, 9, 0), "CarPlay"),                    # second one that morning: ignored
        (t(2, 9, 10), "Built-in Speaker"),
        (t(2, 16, 40), "CarPlay"),                  # at work
        (t(3, 8, 0), "Bluetooth A2DP"),             # not CarPlay
        (t(3, 8, 20), "CarPlay"),
    ]
    tracker = [
        (t(2, 0, 0), "home"),
        (t(2, 8, 45), "not_home"),
        (t(2, 16, 0), "not_home"),
        (t(3, 0, 0), "home"),
    ]
    found = samples_from_history(signal, tracker, "carplay", UTC)
    assert found["home"] == {"2026-11-02": 8 * 60 + 5, "2026-11-03": 8 * 60 + 20}
    assert found["work"] == {"2026-11-02": 16 * 60 + 40}


# --- the learner ---------------------------------------------------------------

LEARN = {
    "learn_enabled": True,
    "learn_entity": SIGNAL,
    "learn_state": "CarPlay",
    "learn_tracker": TRACKER,
    "learn_margin": 5,
}


@pytest.fixture
async def learning(hass, cloud):
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(
        domain=DOMAIN, unique_id=EMAIL, data={"email": EMAIL, "password": PASSWORD},
        options={**LEARN, "schedule_enabled": True, "ready_by": "08:10:00",
                 "weekdays": ["mon", "tue", "wed", "thu", "fri"],
                 "temperature_entity": OUTSIDE, "notify_service": "notify.phone"},
    )
    e.add_to_hass(hass)
    notes = async_mock_service(hass, "notify", "phone")
    hass.states.async_set(OUTSIDE, "-12", {"unit_of_measurement": "°C"})
    hass.states.async_set(TRACKER, "home")
    hass.states.async_set(SIGNAL, "Built-in Speaker")
    await setup(hass, e)
    return e, notes


def teach(entry, kind, minutes_list, days=None):
    learner = entry.runtime_data.learner
    for day, minutes in zip(days or wednesdays(len(minutes_list)), minutes_list):
        learner._samples[kind][day.isoformat()] = minutes


async def test_a_connection_at_home_in_the_morning_is_recorded_once(hass, learning, freezer):
    entry, _ = learning
    freezer.move_to(WED.replace(hour=8, minute=5))
    hass.states.async_set(SIGNAL, "CarPlay")
    await hass.async_block_till_done()
    learner = entry.runtime_data.learner
    assert learner._samples["home"] == {"2026-11-04": 8 * 60 + 5}
    hass.states.async_set(SIGNAL, "Built-in Speaker")
    freezer.move_to(WED.replace(hour=9, minute=0))
    hass.states.async_set(SIGNAL, "CarPlay")
    await hass.async_block_till_done()
    assert learner._samples["home"] == {"2026-11-04": 8 * 60 + 5}


async def test_a_phone_that_just_left_home_is_still_leaving_home(hass, learning, freezer):
    entry, _ = learning
    freezer.move_to(WED.replace(hour=8, minute=0))
    hass.states.async_set(TRACKER, "not_home")           # left home, connects a few minutes later
    await hass.async_block_till_done()
    freezer.move_to(WED.replace(hour=8, minute=4))
    hass.states.async_set(SIGNAL, "CarPlay")
    await hass.async_block_till_done()
    assert entry.runtime_data.learner._samples["home"] == {"2026-11-04": 8 * 60 + 4}


async def test_nothing_is_recorded_when_learning_is_off(hass, cloud):
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD},
                        options={**LEARN, "learn_enabled": False})
    e.add_to_hass(hass)
    hass.states.async_set(TRACKER, "home")
    hass.states.async_set(SIGNAL, "Built-in Speaker")
    await setup(hass, e)
    hass.states.async_set(SIGNAL, "CarPlay")
    await hass.async_block_till_done()
    assert e.runtime_data.learner._samples["home"] == {}


async def test_too_few_days_means_no_learned_time(hass, learning):
    entry, _ = learning
    teach(entry, "home", [8 * 60, 8 * 60 + 20])
    learner = entry.runtime_data.learner
    assert learner.learned_time("home", 2, WED.date()) is None


async def test_the_early_end_minus_the_margin_is_used(hass, learning):
    entry, _ = learning
    teach(entry, "home", [8 * 60 + 30, 8 * 60, 8 * 60 + 15, 8 * 60 + 10, 8 * 60 + 40, 8 * 60 + 5, 8 * 60 + 20, 8 * 60 + 25])
    learner = entry.runtime_data.learner
    # sorted: 480 485 490 495 500 510 520 540 -> early quarter is 485; minus 5 is 480
    assert learner.learned_time("home", 2, WED.date()) == time(8, 0)


async def test_start_times_follow_the_learned_time(hass, learning):
    entry, _ = learning
    teach(entry, "home", [7 * 60 + 50] * 4)       # leaves 07:50, ready by 07:45
    scheduler = entry.runtime_data.scheduler
    cfg = scheduler.config_for(WED.date())
    assert cfg.ready_by == time(7, 45)
    assert [t.strftime("%H:%M") for t in start_times(cfg)] == ["06:05", "06:35", "07:05"]
    # another weekday keeps the fixed time
    assert scheduler.config_for(WED.date() + timedelta(days=1)).ready_by == time(8, 10)


async def test_the_learned_time_stays_within_two_hours_of_the_fixed_one(hass, learning):
    entry, _ = learning
    teach(entry, "home", [4 * 60] * 4)
    assert entry.runtime_data.scheduler.config_for(WED.date()).ready_by == time(6, 10)


async def test_a_new_sample_moves_the_registered_wakeups(hass, learning):
    entry, _ = learning
    scheduler = entry.runtime_data.scheduler
    assert len(scheduler._unsubs) == 3
    teach(entry, "home", [7 * 60 + 50] * 4)
    entry.runtime_data.learner._notify_listeners()
    assert len(scheduler._unsubs) == 3            # re-registered for the learned times


async def test_learned_time_is_used_when_starting(hass, learning, cloud):
    entry, notes = learning
    teach(entry, "home", [7 * 60 + 50] * 4)
    # ready by 07:45 and -12 °C is the 100 minute band, so it starts at 06:05
    await entry.runtime_data.scheduler.async_run(time(6, 5), WED.replace(hour=6, minute=5))
    put = calls_to(cloud, "PUT", "/heaters/1")[-1][2]
    assert put["runtime"] == 100
    assert "ready by 07:45" in notes[-1].data["message"]


async def test_the_evening_before_the_learned_time_is_announced(hass, learning, freezer):
    entry, notes = learning
    teach(entry, "home", [7 * 60 + 50] * 4)
    await entry.runtime_data.scheduler._async_announce(WED.replace(hour=20) - timedelta(days=1))
    assert notes[-1].data["title"] == "Car ready time"
    assert "07:45" in notes[-1].data["message"]


async def test_no_announcement_when_nothing_was_learned(hass, learning):
    entry, notes = learning
    await entry.runtime_data.scheduler._async_announce(WED.replace(hour=20) - timedelta(days=1))
    assert notes == []


async def test_home_leg_is_skipped_when_the_phone_is_away(hass, learning, cloud):
    entry, notes = learning
    hass.states.async_set(TRACKER, "not_home")
    scheduler = entry.runtime_data.scheduler
    await scheduler.async_run(time(6, 30), WED.replace(hour=6, minute=30))
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    assert "not at home" in scheduler.last_result


async def test_backfill_learns_from_recorded_history(hass, learning, monkeypatch):
    entry, _ = learning
    learner = entry.runtime_data.learner
    learner._samples = {"home": {}, "work": {}}
    learner._backfilled = "1|old|rules"

    async def fake(start, end, entity_ids):
        return {
            SIGNAL: [
                (datetime(2026, 10, 20, 0, 0, tzinfo=UTC), "Built-in Speaker"),
                (datetime(2026, 10, 28, 8, 3, tzinfo=UTC), "CarPlay"),
            ],
            TRACKER: [(datetime(2026, 10, 27, 0, 0, tzinfo=UTC), "home")],
        }

    monkeypatch.setattr(learner, "_async_fetch_history", fake)
    await learner.async_backfill()
    assert learner._samples["home"] == {"2026-10-28": 8 * 60 + 3}
    await learner.async_backfill()                 # only once per setting


async def test_the_learned_sensors(hass, learning):
    entry, _ = learning
    state = hass.states.get("sensor.van_learned_leave_home_time")
    assert state.state == "unknown"
    teach(entry, "home", [7 * 60 + 50] * 4)
    entry.runtime_data.learner._notify_listeners()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.van_learned_leave_home_time")
    assert state.attributes["learned"]["wed"] == "07:45"
    assert state.attributes["samples"]["wed"] == 4
    assert hass.states.get("sensor.van_learned_leave_work_time") is not None


# --- leaving work --------------------------------------------------------------

WORK = {
    "work_enabled": True,
    "work_ready_by": "16:30:00",
    "work_weekdays": ["mon", "tue", "wed", "thu", "fri"],
}


@pytest.fixture
async def at_work(hass, cloud, monkeypatch):
    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(
        domain=DOMAIN, unique_id=EMAIL, data={"email": EMAIL, "password": PASSWORD},
        options={**LEARN, **WORK, "schedule_enabled": True, "ready_by": "08:10:00",
                 "temperature_entity": OUTSIDE, "notify_service": "notify.phone"},
    )
    e.add_to_hass(hass)
    notes = async_mock_service(hass, "notify", "phone")
    hass.states.async_set(OUTSIDE, "5", {"unit_of_measurement": "°C"})   # warm at home
    hass.states.async_set(TRACKER, "not_home")
    hass.states.async_set(SIGNAL, "Built-in Speaker")
    set_heater_reply(cloud, heater(lastMeasuredTemperature={"temperature": -12}))
    await setup(hass, e)
    return e, notes


def work_run(entry, hh, mm):
    return entry.runtime_data.work_scheduler.async_run(
        time(hh, mm), WED.replace(hour=hh, minute=mm))


async def test_work_leg_uses_the_cars_fresh_reading_not_the_home_sensor(hass, at_work, cloud):
    entry, notes = at_work
    # ready 16:30; -12 °C is the 100 minute band, so it starts at 14:50
    await work_run(entry, 14, 50)
    assert len(calls_to(cloud, "POST", "/temperature")) == 1
    put = calls_to(cloud, "PUT", "/heaters/1")[-1][2]
    assert put["operationMode"] == "HEATING" and put["runtime"] == 100
    assert notes[-1].data["title"].startswith("At work: ")
    assert "ready by 16:30" in notes[-1].data["message"]


async def test_work_leg_uses_a_sensor_chosen_for_work(hass, at_work, cloud):
    entry, _ = at_work
    hass.states.async_set("sensor.work_temp", "-5", {"unit_of_measurement": "°C"})
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "work_temperature_entity": "sensor.work_temp"})
    await hass.async_block_till_done()
    await work_run(entry, 15, 20)                    # -5 is the 70 minute band
    assert calls_to(cloud, "POST", "/temperature") == []
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 70


async def test_work_leg_accepts_a_weather_entity(hass, at_work, cloud):
    entry, _ = at_work
    hass.states.async_set("weather.work", "cloudy", {"temperature": -5, "temperature_unit": "°C"})
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "work_temperature_entity": "weather.work"})
    await hass.async_block_till_done()
    await work_run(entry, 15, 20)
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 70


async def test_work_leg_falls_back_to_the_car_when_its_sensor_is_unavailable(hass, at_work, cloud):
    entry, _ = at_work
    hass.states.async_set("sensor.work_temp", "unavailable")
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "work_temperature_entity": "sensor.work_temp"})
    await hass.async_block_till_done()
    await work_run(entry, 14, 50)                    # the car says -12
    assert len(calls_to(cloud, "POST", "/temperature")) == 1
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 100


async def test_work_leg_without_a_car_reading_does_not_start(hass, at_work, cloud):
    entry, notes = at_work
    set_heater_reply(cloud, heater())                       # the car has no reading
    await entry.runtime_data.async_refresh()
    for hh, mm in ((14, 50), (15, 20), (15, 50)):
        await work_run(entry, hh, mm)
    assert calls_to(cloud, "PUT", "/heaters/1") == []       # the warm home sensor is not used
    assert "not started" in notes[-1].data["title"]


async def test_work_leg_does_nothing_when_the_phone_is_at_home(hass, at_work, cloud):
    entry, _ = at_work
    hass.states.async_set(TRACKER, "home")
    await work_run(entry, 14, 50)
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    assert "phone is at home" in entry.runtime_data.work_scheduler.last_result


async def test_work_leg_follows_the_schedule_switch(hass, at_work, cloud):
    entry, _ = at_work
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "schedule_enabled": False})
    await hass.async_block_till_done()
    assert entry.runtime_data.work_scheduler._unsubs == []
    await work_run(entry, 14, 50)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_work_leg_is_off_unless_chosen(hass, at_work):
    entry, _ = at_work
    assert len(entry.runtime_data.work_scheduler._unsubs) == 3
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "work_enabled": False})
    await hass.async_block_till_done()
    assert entry.runtime_data.work_scheduler._unsubs == []


async def test_work_leg_learns_its_own_ready_time(hass, at_work):
    entry, _ = at_work
    teach(entry, "work", [16 * 60 + 5] * 4)           # leaves work about 16:05
    cfg = entry.runtime_data.work_scheduler.config_for(WED.date())
    assert cfg.ready_by == time(16, 0)
    # the morning schedule is not touched
    assert entry.runtime_data.scheduler.config_for(WED.date()).ready_by == time(8, 10)


async def test_work_config_defaults():
    cfg = ScheduleConfig.from_options({"schedule_enabled": True, "work_enabled": True}, "work")
    assert cfg.enabled and cfg.ready_by == time(16, 30)
    off = ScheduleConfig.from_options({"schedule_enabled": True}, "work")
    assert not off.enabled


# --- options flow --------------------------------------------------------------

async def test_work_options_saved(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "work")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "work_enabled": True, "work_ready_by": "17:00:00",
        "work_weekdays": ["mon", "tue"]})
    assert r["type"] == "create_entry"
    assert entry.options["work_ready_by"] == "17:00:00"
    assert entry.options["work_temperature_entity"] is None


async def test_work_needs_a_day(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "work")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "work_enabled": True, "work_ready_by": "17:00:00", "work_weekdays": []})
    assert r["errors"] == {"base": "no_weekdays"}


async def test_learning_options_saved(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "learning")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "learn_enabled": True, "learn_entity": SIGNAL, "learn_state": "CarPlay",
        "learn_tracker": TRACKER, "learn_margin": 10})
    assert r["type"] == "create_entry"
    assert entry.options["learn_margin"] == 10
    assert entry.runtime_data.learner.config.enabled


async def test_learning_needs_both_signals(hass, entry, cloud):
    await setup(hass, entry)
    r = await open_options(hass, entry, "learning")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "learn_enabled": True, "learn_entity": SIGNAL, "learn_state": "CarPlay",
        "learn_margin": 5})
    assert r["errors"] == {"base": "learn_needs_signal"}
