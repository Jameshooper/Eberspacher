"""Functional tests: whole days on a simulated clock (docs/functional-test-plan.md).

The other test files call the schedule directly. Here nothing is called by hand: the
clock is moved and Home Assistant fires the wake-ups itself, as it would at home. The
cloud is mocked, so no real heater is ever started.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.eberspaecher import schedule as schedule_module
from custom_components.eberspaecher.const import DOMAIN

from .conftest import EMAIL, PASSWORD, calls_to, heater
from .test_integration import open_options, set_heater_reply, setup

HEL = ZoneInfo("Europe/Helsinki")
OUTSIDE = "sensor.outside"
SIGNAL = "sensor.phone_audio_output"
TRACKER = "device_tracker.phone"
SCHEDULE = "switch.van_schedule"
PAUSE = "date.van_pause_until"

HOME = {
    "schedule_enabled": True,
    "ready_by": "08:10:00",
    "weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "temperature_entity": OUTSIDE,
    "notify_service": "notify.phone",
}
LEARN = {
    "learn_enabled": True,
    "learn_entity": SIGNAL,
    "learn_state": "CarPlay",
    "learn_tracker": TRACKER,
    "learn_margin": 5,
}
WORK = {
    "work_enabled": True,
    "work_ready_by": "16:30:00",
    "work_weekdays": ["mon", "tue", "wed", "thu", "fri"],
}


def local(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=HEL)


MON = local(2026, 11, 2)   # Monday 2 Nov 2026
WED = local(2026, 11, 4)


async def clock(hass, freezer, when):
    """Move the clock to `when` and let Home Assistant run everything that was due.

    Timers fire a moment after the exact second, so the clock goes one second further.
    """
    when = when + timedelta(seconds=1)
    freezer.move_to(when)
    async_fire_time_changed(hass, dt_util.as_utc(when))
    await hass.async_block_till_done()


async def walk(hass, freezer, until, step=timedelta(minutes=30)):
    """Move the clock forward in small steps, so every timer fires on time, in order."""
    now = dt_util.now()
    while now + step < until:
        now += step
        await clock(hass, freezer, now)
    await clock(hass, freezer, until)


async def run_day(hass, freezer, day, times):
    for t in times:
        await clock(hass, freezer, datetime.combine(day, t, HEL))


def starts(cloud):
    return [c[2] for c in calls_to(cloud, "PUT", "/heaters/1")]


@pytest.fixture
async def home(hass, cloud, freezer):
    """The schedule for leaving home, set up at midnight on Wednesday, Helsinki time."""
    return await make(hass, freezer, HOME, WED)


async def make(hass, freezer, options, when, outside="-12", tracker="home"):
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(when)
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD}, options=dict(options))
    e.add_to_hass(hass)
    notes = async_mock_service(hass, "notify", "phone")
    hass.states.async_set(OUTSIDE, outside, {"unit_of_measurement": "°C"})
    hass.states.async_set(TRACKER, tracker)
    hass.states.async_set(SIGNAL, "Built-in Speaker")
    await setup(hass, e)
    # Let the nightly replan (00:00:05) run now, as it would have at home.
    await clock(hass, freezer, when.replace(second=5))
    return e, notes


WAKEUPS = (time(6, 30), time(7, 0), time(7, 30))


# --- FT-DAY: one morning, from the clock ------------------------------------------

@pytest.mark.parametrize(
    ("outside", "start_at", "runtime"),
    [("-12", time(6, 30), 100), ("-5", time(7, 0), 70), ("4", time(7, 30), 40)],
)
async def test_ft_day_01_cold_morning_starts_once_at_the_bands_time(
    hass, cloud, freezer, outside, start_at, runtime
):
    entry, notes = await make(hass, freezer, HOME, WED, outside)
    for t in WAKEUPS:
        await clock(hass, freezer, datetime.combine(WED.date(), t, HEL))
        expected = 1 if t >= start_at else 0
        assert len(starts(cloud)) == expected, t
    assert starts(cloud)[0]["runtime"] == runtime
    assert [n.data["title"] for n in notes] == ["Car heating started"]
    assert "ready by 08:10" in notes[0].data["message"]
    attrs = hass.states.get(SCHEDULE).attributes
    assert attrs["last_result"] == f"Heating {runtime} min at {start_at:%H:%M}"


async def test_ft_day_02_warm_morning_does_not_start_and_says_so_once(hass, cloud, freezer):
    entry, notes = await make(hass, freezer, HOME, WED, "14")
    await run_day(hass, freezer, WED.date(), WAKEUPS)
    assert starts(cloud) == []
    assert len(notes) == 1
    assert "not needed" in entry.runtime_data.scheduler.last_result.casefold()


async def test_ft_day_03_a_full_week_starts_on_weekdays_only(hass, cloud, freezer):
    await make(hass, freezer, HOME, MON)
    for offset in range(7):
        day = MON.date() + timedelta(days=offset)
        await run_day(hass, freezer, day, (time(0, 0, 5), *WAKEUPS))
    assert len(starts(cloud)) == 5


async def test_ft_day_04_the_heater_already_running_is_left_alone(hass, cloud, freezer):
    entry, notes = await make(hass, freezer, HOME, WED)
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await entry.runtime_data.async_refresh()
    await run_day(hass, freezer, WED.date(), WAKEUPS)
    assert starts(cloud) == []


async def test_ft_day_05_phone_away_from_home_skips_the_morning(hass, cloud, freezer):
    entry, _ = await make(hass, freezer, {**HOME, **LEARN}, WED, tracker="not_home")
    await run_day(hass, freezer, WED.date(), WAKEUPS)
    assert starts(cloud) == []
    assert "not at home" in entry.runtime_data.scheduler.last_result


# --- FT-PAUSE: a pause that starts and ends while Home Assistant runs --------------

async def test_ft_pause_01_set_skip_resume(hass, home, cloud, freezer):
    entry, notes = home
    await clock(hass, freezer, WED.replace(hour=1))
    thursday = WED.date() + timedelta(days=1)
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": thursday.isoformat()},
                                   blocking=True)
    for offset in (0, 1):                                   # Wednesday and Thursday
        day = WED.date() + timedelta(days=offset)
        await run_day(hass, freezer, day, (time(0, 0, 5), *WAKEUPS))
    assert starts(cloud) == [] and notes == []
    assert hass.states.get(SCHEDULE).attributes["last_result"] == f"Paused until {thursday}"
    friday = WED.date() + timedelta(days=2)
    await run_day(hass, freezer, friday, (time(0, 0, 5), *WAKEUPS))
    assert len(starts(cloud)) == 1
    assert hass.states.get(SCHEDULE).attributes["last_result"] == "Heating 100 min at 06:30"


async def test_ft_pause_02_clearing_the_pause_mid_morning_lets_the_next_wakeup_run(
    hass, home, cloud, freezer
):
    entry, _ = home
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": WED.date().isoformat()},
                                   blocking=True)
    hass.states.async_set(OUTSIDE, "-5")                    # the 07:00 band
    await clock(hass, freezer, WED.replace(hour=6, minute=30))
    assert starts(cloud) == []
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": "2026-01-01"}, blocking=True)
    assert hass.states.get(SCHEDULE).attributes["last_result"] is None
    await clock(hass, freezer, WED.replace(hour=7))
    assert starts(cloud)[0]["runtime"] == 70


# --- FT-LEARN: learning over several weeks, then using it ---------------------------

async def connect(hass, freezer, when):
    """Get in the car: the phone switches to CarPlay, then back."""
    await walk(hass, freezer, when)
    hass.states.async_set(SIGNAL, "CarPlay")
    await hass.async_block_till_done()
    await clock(hass, freezer, when + timedelta(minutes=40))
    hass.states.async_set(SIGNAL, "Built-in Speaker")
    await hass.async_block_till_done()


async def test_ft_learn_01_three_wednesdays_move_the_fourth(hass, cloud, freezer):
    first = local(2026, 10, 14)                              # a Wednesday
    entry, notes = await make(hass, freezer, {**HOME, **LEARN}, first)
    for week, minute in enumerate((35, 40, 50)):
        day = first + timedelta(weeks=week)
        await connect(hass, freezer, day.replace(hour=7, minute=minute))
    learner = entry.runtime_data.learner
    assert learner.samples("home", 2, date(2026, 11, 4)) == [455, 460, 470]

    # The evening before, the learned time is announced.
    await walk(hass, freezer, local(2026, 11, 3, 20, 0))
    assert notes[-1].data["title"] == "Car ready time"
    assert "07:35" in notes[-1].data["message"]

    # On the day, the wake-ups are planned from 07:35 (median 07:40, minus 5).
    await walk(hass, freezer, local(2026, 11, 4, 0, 0, 5))
    assert hass.states.get(SCHEDULE).attributes["ready_by"] == "07:35"
    assert hass.states.get("sensor.van_learned_leave_home_time").state == "07:35"
    cloud.mock_calls.clear()
    await clock(hass, freezer, local(2026, 11, 4, 5, 55))   # 07:35 minus 100 min
    assert starts(cloud)[0]["runtime"] == 100


async def test_ft_learn_02_the_next_day_goes_back_to_the_fixed_time(hass, cloud, freezer):
    first = local(2026, 10, 14)
    entry, _ = await make(hass, freezer, {**HOME, **LEARN}, first)
    for week in range(3):
        await connect(hass, freezer, (first + timedelta(weeks=week)).replace(hour=7, minute=40))
    await walk(hass, freezer, local(2026, 11, 5, 0, 0, 5))   # Thursday: nothing learned
    attrs = hass.states.get(SCHEDULE).attributes
    assert attrs["ready_by"] == "08:10"
    cloud.mock_calls.clear()
    await clock(hass, freezer, local(2026, 11, 5, 5, 55))
    assert starts(cloud) == []
    await clock(hass, freezer, local(2026, 11, 5, 6, 30))
    assert len(starts(cloud)) == 1


async def test_ft_learn_03_learned_days_survive_a_restart(hass, cloud, freezer):
    first = local(2026, 10, 14)
    entry, _ = await make(hass, freezer, {**HOME, **LEARN}, first)
    for week in range(3):
        await connect(hass, freezer, (first + timedelta(weeks=week)).replace(hour=7, minute=40))
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    learner = entry.runtime_data.learner
    assert learner.learned_time("home", 2, date(2026, 11, 4)) == time(7, 35)


# --- FT-WORK: a working day with leaving work --------------------------------------

async def test_ft_work_01_a_working_day(hass, cloud, freezer, monkeypatch):
    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)
    options = {**HOME, **LEARN, **WORK}
    entry, notes = await make(hass, freezer, options, WED, outside="5")
    set_heater_reply(cloud, heater(lastMeasuredTemperature={"temperature": -12}))
    await entry.runtime_data.async_refresh()

    # Morning at home: 5 °C is the mild band, start 07:30 for 40 min.
    await run_day(hass, freezer, WED.date(), WAKEUPS)
    assert [s["runtime"] for s in starts(cloud)] == [40]

    # Drive to work.
    await clock(hass, freezer, WED.replace(hour=8, minute=5))
    hass.states.async_set(TRACKER, "not_home")
    await hass.async_block_till_done()

    # Afternoon: the car itself says -12 °C, so 100 min before 16:30 is 14:50.
    for t in (time(14, 50), time(15, 20), time(15, 50)):
        await clock(hass, freezer, datetime.combine(WED.date(), t, HEL))
    assert [s["runtime"] for s in starts(cloud)] == [40, 100]
    assert len(calls_to(cloud, "POST", "/temperature")) == 1   # asked once, then reused
    assert notes[-1].data["title"].startswith("At work: ")

    # Leaving at 17:00 is learned as leaving work; the morning was learned as leaving home.
    await connect(hass, freezer, WED.replace(hour=17))
    learner = entry.runtime_data.learner
    assert learner._samples["work"] == {"2026-11-04": 17 * 60}


async def test_ft_work_02_a_day_off_at_home_never_starts_the_work_leg(
    hass, cloud, freezer, monkeypatch
):
    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)
    entry, _ = await make(hass, freezer, {**HOME, **LEARN, **WORK}, WED, outside="14")
    for t in (time(14, 50), time(15, 20), time(15, 50)):
        await clock(hass, freezer, datetime.combine(WED.date(), t, HEL))
    assert starts(cloud) == []
    assert calls_to(cloud, "POST", "/temperature") == []
    assert "at home" in entry.runtime_data.work_scheduler.last_result


# --- FT-OPT: changing settings while a day is planned -------------------------------

async def test_ft_opt_01_a_later_ready_time_moves_todays_wakeups(hass, home, cloud, freezer):
    entry, _ = home
    await clock(hass, freezer, WED.replace(hour=5))
    r = await open_options(hass, entry, "schedule")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        **{k: v for k, v in HOME.items() if k != "schedule_enabled"},
        "schedule_enabled": True, "ready_by": "09:00:00"})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    await clock(hass, freezer, WED.replace(hour=6, minute=30))
    assert starts(cloud) == []
    await clock(hass, freezer, WED.replace(hour=7, minute=20))   # 09:00 minus 100
    assert starts(cloud)[0]["runtime"] == 100


async def test_ft_opt_02_switching_the_schedule_off_and_on(hass, home, cloud, freezer):
    entry, _ = home
    await hass.services.async_call("switch", "turn_off", {"entity_id": SCHEDULE}, blocking=True)
    await hass.async_block_till_done()
    await clock(hass, freezer, WED.replace(hour=6, minute=30))
    assert starts(cloud) == []
    hass.states.async_set(OUTSIDE, "-5")
    await hass.services.async_call("switch", "turn_on", {"entity_id": SCHEDULE}, blocking=True)
    await hass.async_block_till_done()
    await clock(hass, freezer, WED.replace(hour=7))
    assert starts(cloud)[0]["runtime"] == 70


async def test_ft_opt_03_new_heating_bands_apply_at_the_next_wakeup(hass, home, cloud, freezer):
    entry, _ = home
    r = await open_options(hass, entry, "heating_bands")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "no_heat_above": 10, "mild_runtime": 30, "cold_below": 0, "cold_runtime": 60,
        "very_cold_below": -15, "very_cold_runtime": 90})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    # -12 is now "cold" (60 min): 08:10 minus 60 is 07:10.
    await run_day(hass, freezer, WED.date(), (time(6, 40), time(7, 10), time(7, 40)))
    assert [s["runtime"] for s in starts(cloud)] == [60]


@pytest.mark.parametrize(
    ("step", "data", "error"),
    [
        ("schedule", {"schedule_enabled": True, "ready_by": "01:00:00",
                      "weekdays": ["mon"]}, "runtime_too_long"),
        ("schedule", {"schedule_enabled": True, "ready_by": "08:00:00", "weekdays": ["mon"],
                      "notify_service": "script.open_garage"}, "notify_not_notify"),
        ("heating_bands", {"no_heat_above": 10, "mild_runtime": 40, "cold_below": 0,
                           "cold_runtime": 70, "very_cold_below": -10,
                           "very_cold_runtime": 600}, "runtime_too_long"),
        ("work", {"work_enabled": True, "work_ready_by": "16:30:00",
                  "work_weekdays": ["mon"]}, "work_needs_tracker"),
        ("learning", {"learn_enabled": True, "learn_state": "CarPlay",
                      "learn_margin": 5}, "learn_needs_signal"),
    ],
)
async def test_ft_opt_04_each_form_error_keeps_the_old_settings(
    hass, home, cloud, step, data, error
):
    entry, _ = home
    before = dict(entry.options)
    r = await open_options(hass, entry, step)
    r = await hass.config_entries.options.async_configure(r["flow_id"], data)
    assert r["errors"] == {"base": error}
    assert dict(entry.options) == before


async def test_ft_opt_05_clearing_optional_fields_clears_them(hass, home, cloud):
    entry, _ = home
    r = await open_options(hass, entry, "schedule")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "schedule_enabled": True, "ready_by": "08:10:00", "weekdays": ["mon"]})
    assert r["type"] == "create_entry"
    assert entry.options["temperature_entity"] is None
    assert entry.options["notify_service"] is None


async def test_ft_opt_06_reminder_settings_saved(hass, home, cloud):
    entry, _ = home
    r = await open_options(hass, entry, "reminder")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "reminder_enabled": False, "reminder_days": 14})
    assert r["type"] == "create_entry"
    assert entry.options["reminder_enabled"] is False and entry.options["reminder_days"] == 14


# --- FT-RESTART: Home Assistant restarts during a morning --------------------------

async def test_ft_restart_01_restart_before_the_wakeup(hass, home, cloud, freezer):
    entry, _ = home
    await clock(hass, freezer, WED.replace(hour=6))
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await run_day(hass, freezer, WED.date(), WAKEUPS)
    assert len(starts(cloud)) == 1


async def test_ft_restart_02_restart_after_a_start_does_not_start_again(
    hass, home, cloud, freezer
):
    """A restart forgets the start in memory; the heater's own state must stop a second one."""
    entry, _ = home
    await clock(hass, freezer, WED.replace(hour=6, minute=30))
    assert len(starts(cloud)) == 1
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(OUTSIDE, "-5")                  # now the 07:00 band
    await clock(hass, freezer, WED.replace(hour=7))
    assert starts(cloud) == []


async def test_ft_restart_03_restart_after_a_finished_short_run_does_not_start_again(
    hass, home, cloud, freezer
):
    """Started at 06:30, the heater was turned off by hand, then Home Assistant restarted
    and it got colder. The car was already warmed once today: no second start."""
    entry, _ = home
    hass.states.async_set(OUTSIDE, "-5")
    await clock(hass, freezer, WED.replace(hour=7))
    assert len(starts(cloud)) == 1
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(OUTSIDE, "2")                   # the 07:30 band
    await clock(hass, freezer, WED.replace(hour=7, minute=30))
    assert len(starts(cloud)) == 1


# --- FT-CLOCK: clock changes and midnight -------------------------------------------

async def test_ft_clock_01_after_spring_forward_it_starts_at_0630_local(hass, cloud, freezer):
    sunday = local(2027, 3, 28, 12)                          # clocks went 03:00 -> 04:00
    await make(hass, freezer, HOME, sunday)
    monday = date(2027, 3, 29)
    await run_day(hass, freezer, monday, (time(0, 0, 5), time(6, 29)))
    assert starts(cloud) == []
    await clock(hass, freezer, datetime.combine(monday, time(6, 30), HEL))
    assert len(starts(cloud)) == 1
    assert dt_util.utcnow().hour == 3                         # 06:30 EEST is 03:30 UTC


async def test_ft_clock_02_after_fall_back_it_starts_at_0630_local(hass, cloud, freezer):
    sunday = local(2027, 10, 31, 12)                         # clocks went 04:00 -> 03:00
    await make(hass, freezer, HOME, sunday)
    monday = date(2027, 11, 1)
    await run_day(hass, freezer, monday, (time(0, 0, 5), time(6, 29)))
    assert starts(cloud) == []
    await clock(hass, freezer, datetime.combine(monday, time(6, 30), HEL))
    assert len(starts(cloud)) == 1
    assert dt_util.utcnow().hour == 4                         # 06:30 EET is 04:30 UTC


async def test_ft_clock_03_a_wakeup_in_the_repeated_hour_starts_once(hass, cloud, freezer):
    """On the autumn change 03:00-04:00 happens twice. A 03:30 wake-up must start once."""
    options = {**HOME, "ready_by": "05:10:00", "weekdays": ["sun"]}
    await make(hass, freezer, options, local(2027, 10, 31))
    utc_start = datetime(2027, 10, 31, 0, 0, tzinfo=dt_util.UTC)   # 03:00 EEST
    for minutes in range(0, 150, 10):                        # walk through both 03:xx hours
        when = utc_start + timedelta(minutes=minutes)
        freezer.move_to(when)
        async_fire_time_changed(hass, when)
        await hass.async_block_till_done()
    assert len(starts(cloud)) == 1


@pytest.mark.xfail(strict=True, reason="Finding FT-3: Home Assistant skips times that do not exist")
async def test_ft_clock_04_a_wakeup_in_the_skipped_hour(hass, cloud, freezer):
    """On the spring change 03:00-04:00 does not exist. A 03:30 wake-up still has to run."""
    options = {**HOME, "ready_by": "05:10:00", "weekdays": ["sun"]}
    await make(hass, freezer, options, local(2027, 3, 28))
    utc_start = datetime(2027, 3, 28, 0, 0, tzinfo=dt_util.UTC)    # 02:00 EET
    for minutes in range(0, 180, 10):
        when = utc_start + timedelta(minutes=minutes)
        freezer.move_to(when)
        async_fire_time_changed(hass, when)
        await hass.async_block_till_done()
    assert len(starts(cloud)) == 1


async def test_ft_clock_05_a_wakeup_that_fires_late_does_not_start(hass, home, cloud, freezer):
    """The host was paused (a sleeping VM or a busy backup) from 06:00 to 09:00. The 06:30
    wake-up then fires at 09:00, after the car should have been ready: it must not start."""
    entry, _ = home
    await clock(hass, freezer, WED.replace(hour=9))
    assert starts(cloud) == []


async def test_ft_restart_04_the_start_day_is_saved_and_deleted_with_the_integration(
    hass, home, cloud, freezer, hass_storage
):
    entry, _ = home
    await clock(hass, freezer, WED.replace(hour=6, minute=30))
    await hass.config_entries.async_unload(entry.entry_id)
    key = f"eberspaecher.{entry.entry_id}.schedule"
    assert hass_storage[key]["data"] == {"started_on": {"home": "2026-11-04"}}
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_ft_restart_05_a_corrupt_saved_start_day_is_ignored(hass, cloud, freezer, hass_storage):
    hass_storage["eberspaecher.ENTRY.schedule"] = {
        "version": 1, "key": "x", "data": {"started_on": {"home": "not-a-day", "work": 5}}}
    entry, _ = await make(hass, freezer, HOME, WED)
    assert entry.runtime_data.started_on == {}
    await run_day(hass, freezer, WED.date(), WAKEUPS)
    assert len(starts(cloud)) == 1


async def test_ft_clock_06_a_wakeup_a_little_late_still_starts(hass, home, cloud):
    """A few minutes late (a busy host) is still in time."""
    entry, _ = home
    await entry.runtime_data.scheduler.async_run(time(6, 30), WED.replace(hour=6, minute=44))
    assert len(starts(cloud)) == 1


async def test_ft_clock_07_a_late_wakeup_says_why(hass, home, cloud):
    entry, notes = home
    await entry.runtime_data.scheduler.async_run(time(6, 30), WED.replace(hour=6, minute=46))
    assert starts(cloud) == [] and notes == []
    assert hass.states.get(SCHEDULE).attributes["last_result"] == (
        "Skipped: the 06:30 wake-up came too late")


# --- FT-CAR: the weekly check that the car answers ----------------------------------

from homeassistant.helpers import issue_registry as ir  # noqa: E402

from custom_components.eberspaecher import carcheck as carcheck_module  # noqa: E402
from custom_components.eberspaecher.api import BASE_URL  # noqa: E402

from .conftest import IMEI  # noqa: E402

SUNDAY = local(2026, 11, 8)


def car_issue(hass):
    return [i for i in ir.async_get(hass).issues.values()
            if i.translation_key == "car_unreachable"]


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(carcheck_module, "ANSWER_WAIT", 0)


def answer_after_request(entry, cloud, reply):
    """The cloud returns `reply` once the car was asked for a temperature."""
    api = entry.runtime_data.api
    real = api.async_request_temperature

    async def _requested(imei):
        await real(imei)
        set_heater_reply(cloud, heater(lastMeasuredTemperature=reply))

    api.async_request_temperature = _requested


async def test_ft_car_01_runs_on_sunday_at_1800_only(hass, cloud, freezer, no_wait):
    await make(hass, freezer, {"notify_service": "notify.phone"}, local(2026, 11, 7, 17))
    await walk(hass, freezer, local(2026, 11, 7, 19))                  # Saturday
    assert calls_to(cloud, "POST", "/temperature") == []
    await walk(hass, freezer, SUNDAY.replace(hour=18, minute=5))
    assert len(calls_to(cloud, "POST", "/temperature")) == 1
    assert starts(cloud) == []                                          # it only reads


async def test_ft_car_02_an_answer_is_ok(hass, cloud, freezer, no_wait):
    entry, notes = await make(hass, freezer, {"notify_service": "notify.phone"}, SUNDAY)
    answer_after_request(entry, cloud, {"temperature": 3, "timestamp": "2026-11-08T16:00:20Z"})
    await entry.runtime_data.car_check.async_check()
    assert entry.runtime_data.car_check.last_ok is True
    assert car_issue(hass) == [] and notes == []


async def test_ft_car_03_no_answer_raises_a_notice_and_a_message(hass, cloud, freezer, no_wait):
    entry, notes = await make(hass, freezer, {"notify_service": "notify.phone"}, SUNDAY)
    await entry.runtime_data.car_check.async_check()          # the reading stays empty
    assert entry.runtime_data.car_check.last_ok is False
    assert len(car_issue(hass)) == 1
    assert notes[-1].data["title"] == "Car unit not answering"
    assert "Van" in notes[-1].data["message"]
    assert len(calls_to(cloud, "GET", "/heaters")) >= 3       # looked three times


async def test_ft_car_04_an_old_reading_is_not_an_answer(hass, cloud, freezer, no_wait):
    old = {"temperature": 3, "timestamp": "2026-11-01T16:00:00Z"}
    entry, _ = await make(hass, freezer, {}, SUNDAY)
    set_heater_reply(cloud, heater(lastMeasuredTemperature=old))
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.car_check.async_check()
    assert entry.runtime_data.car_check.last_ok is False
    assert len(car_issue(hass)) == 1


async def test_ft_car_05_the_notice_goes_once_the_car_answers_again(hass, cloud, freezer, no_wait):
    entry, _ = await make(hass, freezer, {}, SUNDAY)
    await entry.runtime_data.car_check.async_check()
    assert len(car_issue(hass)) == 1
    answer_after_request(entry, cloud, {"temperature": 3, "timestamp": "2026-11-15T16:00:20Z"})
    await entry.runtime_data.car_check.async_check()
    assert car_issue(hass) == []


async def test_ft_car_06_the_notice_goes_once_the_heater_runs(hass, cloud, freezer, no_wait):
    entry, _ = await make(hass, freezer, {}, SUNDAY)
    await entry.runtime_data.car_check.async_check()
    assert len(car_issue(hass)) == 1
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await entry.runtime_data.async_refresh()
    assert car_issue(hass) == []


async def test_ft_car_07_a_running_heater_needs_no_request(hass, cloud, freezer, no_wait):
    entry, _ = await make(hass, freezer, {}, SUNDAY)
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.car_check.async_check()
    assert calls_to(cloud, "POST", "/temperature") == []
    assert entry.runtime_data.car_check.last_ok is True


async def test_ft_car_08_cloud_down_gives_no_verdict(hass, cloud, freezer, no_wait):
    """The cloud has its own notice. A car check that could not ask says nothing."""
    entry, notes = await make(hass, freezer, {"notify_service": "notify.phone"}, SUNDAY)
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/calls/{IMEI}/temperature", status=503)
    await entry.runtime_data.car_check.async_check()
    assert entry.runtime_data.car_check.last_ok is None
    assert car_issue(hass) == [] and notes == []


async def test_ft_car_09_cloud_down_after_asking_gives_no_verdict(hass, cloud, freezer, no_wait):
    entry, notes = await make(hass, freezer, {"notify_service": "notify.phone"}, SUNDAY)
    set_heater_reply(cloud, heater(), status=503)
    await entry.runtime_data.car_check.async_check()
    assert entry.runtime_data.car_check.last_ok is None
    assert car_issue(hass) == [] and notes == []


async def test_ft_car_10_can_be_turned_off(hass, cloud, freezer, no_wait):
    entry, _ = await make(hass, freezer, {}, SUNDAY)
    await entry.runtime_data.car_check.async_check()
    assert len(car_issue(hass)) == 1
    r = await open_options(hass, entry, "reminder")
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "reminder_enabled": True, "reminder_days": 30, "car_check_enabled": False})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    assert car_issue(hass) == []
    cloud.mock_calls.clear()
    await walk(hass, freezer, SUNDAY.replace(hour=18, minute=5))
    assert calls_to(cloud, "POST", "/temperature") == []


async def test_ft_car_11_unload_while_waiting_gives_up(hass, cloud, freezer, monkeypatch):
    entry, notes = await make(hass, freezer, {"notify_service": "notify.phone"}, SUNDAY)
    check = entry.runtime_data.car_check
    real_sleep = carcheck_module.asyncio.sleep

    async def sleep_and_stop(seconds):
        check.async_stop()
        await real_sleep(0)

    monkeypatch.setattr(carcheck_module.asyncio, "sleep", sleep_and_stop)
    await check.async_check()
    assert check.last_ok is None and car_issue(hass) == [] and notes == []


async def test_ft_car_12_diagnostics_show_the_last_check(hass, cloud, freezer, no_wait):
    from custom_components.eberspaecher.diagnostics import async_get_config_entry_diagnostics

    entry, _ = await make(hass, freezer, {}, SUNDAY)
    await entry.runtime_data.car_check.async_check()
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["car_check"]["last_ok"] is False
    assert data["car_check"]["last_checked"] == "2026-11-07T22:00:06+00:00"  # Sunday 00:00:06 local


# --- FT-ONCE: a one-off "be ready by" time ------------------------------------------

ONCE = "datetime.van_ready_by_one_off"
CANCEL = "button.van_cancel_one_off_ready_by"
SUN = local(2026, 11, 8)                                  # a Sunday: not in the weekday list


async def set_once(hass, when):
    await hass.services.async_call(
        "datetime", "set_value",
        {"entity_id": ONCE, "datetime": when.isoformat()}, blocking=True)
    await hass.async_block_till_done()


async def make_once(hass, freezer, outside="-12", at=(6, 0), options=HOME):
    entry, notes = await make(hass, freezer, options, SUN.replace(hour=at[0], minute=at[1]), outside)
    return entry, notes


@pytest.mark.parametrize(
    ("outside", "runtime", "start"),
    [("-12", 100, (8, 20)), ("-5", 70, (8, 50)), ("4", 40, (9, 20))],
)
async def test_ft_once_01_starts_when_needed_for_the_bands_time(
    hass, cloud, freezer, outside, runtime, start
):
    entry, notes = await make_once(hass, freezer, outside)
    await set_once(hass, SUN.replace(hour=10))
    assert hass.states.get(ONCE).attributes["start_times"] == ["08:20", "08:50", "09:20"]
    for wake in ((8, 20), (8, 50), (9, 20)):
        before = len(starts(cloud))
        await clock(hass, freezer, SUN.replace(hour=wake[0], minute=wake[1]))
        assert (len(starts(cloud)) - before == 1) == (wake == start), wake
    assert starts(cloud)[0]["runtime"] == runtime
    assert notes[-1].data["title"] == "Car heating started"
    assert "ready by 10:00" in notes[-1].data["message"]


async def test_ft_once_02_clears_itself_and_leaves_the_schedule_alone(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert len(starts(cloud)) == 1
    state = hass.states.get(ONCE)
    assert state.state == "unknown"
    assert state.attributes["last_result"] == "Heating 100 min at 08:20"
    assert entry.runtime_data.started_on == {}                 # the normal schedule is untouched
    await clock(hass, freezer, SUN.replace(hour=8, minute=50))
    assert len(starts(cloud)) == 1


async def test_ft_once_03_set_late_starts_at_once(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer, at=(9, 0))
    await set_once(hass, SUN.replace(hour=10))                # 100 min needed, 60 left
    assert [s["runtime"] for s in starts(cloud)] == [100]
    assert hass.states.get(ONCE).state == "unknown"


async def test_ft_once_04_warm_day_says_not_needed_and_clears(hass, cloud, freezer):
    entry, notes = await make_once(hass, freezer, outside="14")
    await set_once(hass, SUN.replace(hour=10))
    for wake in ((8, 20), (8, 50)):
        await clock(hass, freezer, SUN.replace(hour=wake[0], minute=wake[1]))
        assert notes == [] and hass.states.get(ONCE).state != "unknown"
    await clock(hass, freezer, SUN.replace(hour=9, minute=20))
    assert starts(cloud) == []
    assert "not needed" in notes[-1].data["title"]
    assert hass.states.get(ONCE).state == "unknown"


async def test_ft_once_05_ignores_weekdays_pause_and_the_phone(
    hass, cloud, freezer, monkeypatch
):
    from custom_components.eberspaecher import schedule as schedule_module
    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)
    entry, _ = await make_once(hass, freezer, options={**HOME, **LEARN})
    hass.states.async_set(TRACKER, "not_home")
    await hass.services.async_call("date", "set_value",
                                   {"entity_id": PAUSE, "date": "2026-11-10"}, blocking=True)
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert len(starts(cloud)) == 1


async def test_ft_once_06_cancel_with_the_button(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=10))
    await hass.services.async_call("button", "press", {"entity_id": CANCEL}, blocking=True)
    assert hass.states.get(ONCE).state == "unknown"
    await run_day(hass, freezer, SUN.date(), (time(8, 20), time(8, 50), time(9, 20)))
    assert starts(cloud) == []


async def test_ft_once_07_a_time_in_the_past_is_not_planned(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=5))
    state = hass.states.get(ONCE)
    assert state.state == "unknown" and state.attributes["start_times"] == []
    assert state.attributes["last_result"] == "One-off ready time passed without a start"
    assert starts(cloud) == []


async def test_ft_once_08_a_new_time_replaces_the_old_plan(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=10))
    await set_once(hass, SUN.replace(hour=12))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert starts(cloud) == []
    await clock(hass, freezer, SUN.replace(hour=10, minute=20))
    assert len(starts(cloud)) == 1


async def test_ft_once_09_survives_a_restart(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=10))
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.ready_once.ready_at == SUN.replace(hour=10).astimezone(dt_util.UTC)
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert len(starts(cloud)) == 1


async def test_ft_once_10_a_restart_after_the_time_has_passed_drops_it(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=9))
    await hass.config_entries.async_unload(entry.entry_id)
    await clock(hass, freezer, SUN.replace(hour=10))
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.ready_once.ready_at is None
    assert starts(cloud) == []


async def test_ft_once_11_a_running_heater_is_left_alone(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer)
    set_heater_reply(cloud, heater(state="ON", heaterState="ON"))
    await entry.runtime_data.async_refresh()
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert starts(cloud) == []
    assert hass.states.get(ONCE).state == "unknown"
    assert "already running" in hass.states.get(ONCE).attributes["last_result"]


async def test_ft_once_12_a_failed_start_tries_again_at_the_next_wakeup(hass, cloud, freezer):
    entry, notes = await make_once(hass, freezer, outside="-5")
    await set_once(hass, SUN.replace(hour=10))
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    cloud.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater()])
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", status=500)
    await clock(hass, freezer, SUN.replace(hour=8, minute=50))
    assert "failed" in notes[-1].data["title"]
    assert hass.states.get(ONCE).state != "unknown"           # still planned
    set_heater_reply(cloud, heater())
    await clock(hass, freezer, SUN.replace(hour=9, minute=20))
    assert len(starts(cloud)) == 1


async def test_ft_once_13_no_reading_says_so_and_clears(hass, cloud, freezer, monkeypatch):
    from custom_components.eberspaecher import schedule as schedule_module
    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)
    entry, notes = await make_once(hass, freezer)
    hass.states.async_set(OUTSIDE, "unavailable")
    await set_once(hass, SUN.replace(hour=10))
    for wake in ((8, 20), (8, 50), (9, 20)):
        await clock(hass, freezer, SUN.replace(hour=wake[0], minute=wake[1]))
    assert starts(cloud) == []
    assert "not started" in notes[-1].data["title"]
    assert hass.states.get(ONCE).state == "unknown"


async def test_ft_once_14_other_heating_bands_are_used(hass, cloud, freezer):
    entry, _ = await make_once(hass, freezer, outside="-12")
    r = await open_options(hass, entry, "heating_bands")
    await hass.config_entries.options.async_configure(r["flow_id"], {
        "no_heat_above": 10, "mild_runtime": 30, "cold_below": 0, "cold_runtime": 60,
        "very_cold_below": -10, "very_cold_runtime": 90})
    await hass.async_block_till_done()
    await set_once(hass, SUN.replace(hour=10))
    assert hass.states.get(ONCE).attributes["start_times"] == ["08:30", "09:00", "09:30"]
    await clock(hass, freezer, SUN.replace(hour=8, minute=30))
    assert starts(cloud)[0]["runtime"] == 90


async def test_ft_once_15_diagnostics_show_it(hass, cloud, freezer):
    from custom_components.eberspaecher.diagnostics import async_get_config_entry_diagnostics

    entry, _ = await make_once(hass, freezer)
    await set_once(hass, SUN.replace(hour=10))
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["ready_once"]["ready_at"] == "2026-11-08T08:00:00+00:00"


async def car_says(cloud, entry, temperature):
    set_heater_reply(cloud, heater(lastMeasuredTemperature={"temperature": temperature}))
    await entry.runtime_data.async_refresh()


@pytest.fixture
def car_no_wait(monkeypatch):
    from custom_components.eberspaecher import schedule as schedule_module
    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)


async def test_ft_once_16_phone_away_goes_by_the_car_not_the_home_sensor(
    hass, cloud, freezer, car_no_wait
):
    entry, _ = await make_once(hass, freezer, outside="14", options={**HOME, **LEARN})
    hass.states.async_set(TRACKER, "not_home")
    await car_says(cloud, entry, -12)                         # warm at home, freezing at the car
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert [s["runtime"] for s in starts(cloud)] == [100]
    assert len(calls_to(cloud, "POST", "/temperature")) == 1


async def test_ft_once_17_phone_home_goes_by_the_home_sensor_not_the_car(
    hass, cloud, freezer, car_no_wait
):
    entry, _ = await make_once(hass, freezer, outside="-12", options={**HOME, **LEARN})
    await car_says(cloud, entry, 14)                          # freezing at home, warm car reading
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert [s["runtime"] for s in starts(cloud)] == [100]
    assert calls_to(cloud, "POST", "/temperature") == []


async def test_ft_once_18_phone_away_and_a_silent_car_falls_back_to_the_sensor(
    hass, cloud, freezer, car_no_wait
):
    entry, notes = await make_once(hass, freezer, outside="-12", options={**HOME, **LEARN})
    hass.states.async_set(TRACKER, "not_home")                # the cloud has no reading
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert [s["runtime"] for s in starts(cloud)] == [100]
    assert "from sensor.outside" in notes[-1].data["message"]


async def test_ft_once_19_phone_location_unknown_uses_the_sensor(hass, cloud, freezer, car_no_wait):
    entry, _ = await make_once(hass, freezer, outside="-12", options={**HOME, **LEARN})
    hass.states.async_set(TRACKER, "unavailable")
    await car_says(cloud, entry, 14)
    await set_once(hass, SUN.replace(hour=10))
    await clock(hass, freezer, SUN.replace(hour=8, minute=20))
    assert [s["runtime"] for s in starts(cloud)] == [100]
    assert calls_to(cloud, "POST", "/temperature") == []
