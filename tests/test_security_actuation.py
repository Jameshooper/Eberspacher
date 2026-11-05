"""Security tests: what may make the heater start (docs/security-test-plan.md, section D).

Every start goes to the mocked cloud; no real device is ever contacted.
"""
from __future__ import annotations

import asyncio
from datetime import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.eberspaecher.api import BASE_URL
from custom_components.eberspaecher.const import DOMAIN
from custom_components.eberspaecher.learning import MAX_SHIFT

from .conftest import EMAIL, IMEI, PASSWORD, calls_to, heater
from .test_integration import setup
from .test_learning import (  # noqa: F401 - fixtures
    LEARN, TRACKER, WED as LEARN_WED, at_work, learning, teach, work_run,
)
from .test_schedule import SCHEDULE_OPTIONS, SENSOR, at, scheduled  # noqa: F401

OTHER = "OTHER-CAR-0002"
BAD_TEMPERATURES = [
    "nan", "NaN", "inf", "-inf", "1e308", "-1e308", "-273.16", "-61", "71", "--5", "5,0", "",
]
WAKE_UPS = [time(6, 30), time(7, 0), time(7, 30)]


async def run_all(entry):
    for t in WAKE_UPS:
        await entry.runtime_data.scheduler.async_run(t, at(t.hour, t.minute))


# --- SEC-DEV-01 -------------------------------------------------------------------

@pytest.mark.parametrize("value", BAD_TEMPERATURES)
async def test_sec_dev_01_faulty_sensor_never_starts_the_heater(hass, scheduled, cloud, value):
    entry, notes = scheduled
    hass.states.async_set(SENSOR, value, {"unit_of_measurement": "°C"})
    await run_all(entry)
    assert calls_to(cloud, "PUT", "/heaters/1") == []
    assert "not started" in notes[-1].data["title"]


@pytest.mark.parametrize("value", ["nan", float("nan"), float("-inf"), 1e9])
async def test_sec_dev_01_faulty_weather_attribute_never_starts(hass, scheduled, cloud, value):
    entry, _ = scheduled
    hass.states.async_set("weather.home", "cloudy", {"temperature": value})
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "temperature_entity": "weather.home"})
    await hass.async_block_till_done()
    await run_all(entry)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


@pytest.mark.parametrize("value", ["nan", "-inf", -400])
async def test_sec_dev_01_faulty_heater_reading_never_starts(hass, scheduled, cloud, value):
    """With the sensor gone, the heater's own (faulty) reading is the fallback."""
    entry, _ = scheduled
    hass.states.async_set(SENSOR, "unavailable")
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    cloud.get(f"{BASE_URL}/calls/{IMEI}/heaters",
              json=[heater(lastMeasuredTemperature={"temperature": value})])
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    await entry.runtime_data.async_refresh()
    await run_all(entry)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_sec_dev_01_real_cold_still_starts(hass, scheduled, cloud):
    entry, _ = scheduled
    hass.states.async_set(SENSOR, "-60", {"unit_of_measurement": "°C"})   # the coldest plausible
    await run_all(entry)
    assert calls_to(cloud, "PUT", "/heaters/1")[-1][2]["runtime"] == 100


# --- SEC-DEV-02 -------------------------------------------------------------------

@pytest.fixture
async def two_cars(hass, aioclient_mock):
    m = aioclient_mock
    m.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    m.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}, {"imei": OTHER, "name": "Car"}])
    m.get(f"{BASE_URL}/calls/{IMEI}/heaters",
          json=[heater(lastMeasuredTemperature={"temperature": -12})])
    m.get(f"{BASE_URL}/calls/{OTHER}/heaters",
          json=[heater(lastMeasuredTemperature={"temperature": 20})])
    for imei in (IMEI, OTHER):
        m.put(f"{BASE_URL}/calls/{imei}/heaters/1", text="")
        m.post(f"{BASE_URL}/calls/{imei}/temperature", text="")
    await hass.config.async_set_time_zone("UTC")
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD},
                        options={**SCHEDULE_OPTIONS, **LEARN, "work_enabled": True})
    e.add_to_hass(hass)
    async_mock_service(hass, "notify", "phone")
    hass.states.async_set(SENSOR, "-12", {"unit_of_measurement": "°C"})
    hass.states.async_set(TRACKER, "home")
    await setup(hass, e)
    return e, m


def puts_to(mock, imei):
    return [c for c in mock.mock_calls if c[0] == "PUT" and f"/calls/{imei}/" in c[1].path]


async def test_sec_dev_02_schedule_only_starts_its_own_device(hass, two_cars):
    entry, m = two_cars
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert len(puts_to(m, IMEI)) == 1
    assert puts_to(m, OTHER) == []


async def test_sec_dev_02_car_reading_comes_from_the_same_device(hass, two_cars, monkeypatch):
    from custom_components.eberspaecher import schedule as schedule_module

    monkeypatch.setattr(schedule_module, "CAR_READING_WAIT", 0)
    entry, m = two_cars
    hass.states.async_set(TRACKER, "not_home")
    sched = entry.runtime_data.work_scheduler
    await sched.async_run(time(14, 50), LEARN_WED.replace(hour=14, minute=50))
    asked = [c[1].path for c in m.mock_calls if c[0] == "POST" and c[1].path.endswith("/temperature")]
    assert asked and all(f"/calls/{IMEI}/" in p for p in asked)
    assert len(puts_to(m, IMEI)) == 1 and puts_to(m, OTHER) == []   # -12 from its own car


async def test_sec_dev_02_schedule_switch_sits_on_the_controlled_device(hass, two_cars):
    from homeassistant.helpers import entity_registry as er

    entry, _ = two_cars
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("switch", DOMAIN, f"{IMEI}_schedule")
    assert reg.async_get_entity_id("switch", DOMAIN, f"{OTHER}_schedule") is None


# --- SEC-DEV-03 -------------------------------------------------------------------

@pytest.mark.parametrize("place", ["home", "HOME", "Home ", "unavailable", "unknown"])
async def test_sec_dev_03_work_leg_never_starts_at_home_or_unknown(hass, at_work, cloud, place):
    entry, _ = at_work
    hass.states.async_set(TRACKER, place)
    await work_run(entry, 14, 50)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


async def test_sec_dev_03_work_leg_with_a_deleted_tracker_does_not_start(hass, at_work, cloud):
    entry, _ = at_work
    hass.states.async_remove(TRACKER)
    await work_run(entry, 14, 50)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


@pytest.mark.parametrize("place", ["not_home", "Work"])
async def test_sec_dev_03_home_leg_skips_when_away(hass, learning, cloud, place):
    entry, _ = learning
    hass.states.async_set(TRACKER, place)
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert calls_to(cloud, "PUT", "/heaters/1") == []


# --- SEC-DEV-04 -------------------------------------------------------------------

async def test_sec_dev_04_learned_shift_is_bounded(hass, learning):
    entry, _ = learning
    teach(entry, "home", [4 * 60] * 6)                     # poisoned: 04:00 every Wednesday
    cfg = entry.runtime_data.scheduler.config_for(LEARN_WED.date())
    fixed = 8 * 60 + 10
    got = cfg.ready_by.hour * 60 + cfg.ready_by.minute
    assert fixed - got <= MAX_SHIFT


@pytest.mark.parametrize("stored", [
    {"samples": {"home": {"2026-10-28": "abc", "not-a-day": 480, "2026-10-21": 99999}}},
    {"samples": "garbage"},
    {"samples": {"home": ["x"]}, "last_home": 12345},
    ["not", "a", "dict"],
])
async def test_sec_dev_04_corrupt_learning_store_does_not_break_setup(
    hass, cloud, hass_storage, stored
):
    from homeassistant.config_entries import ConfigEntryState

    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD}, options=dict(LEARN))
    e.add_to_hass(hass)
    hass_storage[f"eberspaecher.{e.entry_id}.departures"] = {
        "version": 1, "minor_version": 1, "key": f"eberspaecher.{e.entry_id}.departures",
        "data": stored,
    }
    await setup(hass, e)
    assert e.state is ConfigEntryState.LOADED
    samples = e.runtime_data.learner._samples["home"]
    assert all(0 <= m < 1440 for m in samples.values())


async def test_sec_dev_04_one_sample_per_day_despite_toggling(hass, learning, freezer):
    entry, _ = learning
    from .test_learning import SIGNAL

    for minute in range(0, 50, 2):
        freezer.move_to(LEARN_WED.replace(hour=7, minute=minute))
        hass.states.async_set(SIGNAL, "CarPlay" if minute % 4 == 0 else "Speaker")
        await hass.async_block_till_done()
    assert entry.runtime_data.learner._samples["home"] == {"2026-11-04": 7 * 60}


# --- SEC-DEV-05 -------------------------------------------------------------------

async def test_sec_dev_05_renewed_but_faulty_car_reading_is_not_used(hass, at_work, cloud, monkeypatch):
    entry, _ = at_work
    old = {"temperature": 5, "timestamp": "2026-11-04T06:00:00Z"}
    new = {"temperature": "nan", "timestamp": "2026-11-04T14:50:20Z"}
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    cloud.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater(lastMeasuredTemperature=old)])
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    cloud.post(f"{BASE_URL}/calls/{IMEI}/temperature", text="")
    await entry.runtime_data.async_refresh()
    request = entry.runtime_data.api.async_request_temperature

    async def answered(imei):
        await request(imei)
        cloud.clear_requests()
        cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
        cloud.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
        cloud.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater(lastMeasuredTemperature=new)])
        cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")

    monkeypatch.setattr(entry.runtime_data.api, "async_request_temperature", answered)
    await work_run(entry, 14, 50)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


# --- SEC-DEV-06 -------------------------------------------------------------------

async def test_sec_dev_06_pause_survives_restart_and_blocks_every_wake_up(hass, scheduled, cloud):
    entry, _ = scheduled
    await hass.services.async_call("date", "set_value",
        {"entity_id": "date.van_pause_until", "date": "2026-11-04"}, blocking=True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await run_all(entry)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


# --- SEC-DEV-07 -------------------------------------------------------------------

async def test_sec_dev_07_concurrent_wake_ups_start_once(hass, scheduled, cloud):
    entry, _ = scheduled
    sched = entry.runtime_data.scheduler
    await asyncio.gather(*(sched.async_run(time(6, 30), at(6, 30)) for _ in range(5)))
    assert len(calls_to(cloud, "PUT", "/heaters/1")) == 1


async def test_sec_dev_07_running_heater_is_not_started_again(hass, scheduled, cloud):
    entry, _ = scheduled
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    cloud.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater(state="ON")])
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert calls_to(cloud, "PUT", "/heaters/1") == []


# --- SEC-DEV-08 -------------------------------------------------------------------

async def test_sec_dev_08_failed_stop_keeps_showing_on(hass, entry, aioclient_mock):
    from homeassistant.exceptions import HomeAssistantError

    from .test_integration import SWITCH, set_heater_reply

    set_heater_reply(aioclient_mock, heater(state="ON"))
    await setup(hass, entry)
    assert hass.states.get(SWITCH).state == "on"
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    aioclient_mock.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", status=500)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    assert hass.states.get(SWITCH).state == "on"


async def test_sec_dev_08_cloud_down_during_start_tells_the_user(hass, scheduled, cloud):
    entry, notes = scheduled
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    cloud.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", exc=TimeoutError())
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert "failed" in notes[-1].data["title"]
    assert entry.runtime_data.scheduler.last_result.startswith("Failed")


# --- SEC-DEV-09 / SEC-DEV-10 --------------------------------------------------------

@pytest.mark.parametrize("bad", ["../../x", "a?b=c", "a#b", "a/b", "a b", "x" * 65, "..", "a.b"])
async def test_sec_dev_09_unusable_device_ids_are_never_put_in_a_path(hass, aioclient_mock, bad):
    from custom_components.eberspaecher.api import EberspaecherApi
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(f"{BASE_URL}/calls", json=[{"imei": bad}, {"imei": IMEI}])
    api = EberspaecherApi(async_get_clientsession(hass), EMAIL, PASSWORD)
    devices = await api.async_get_calls()
    assert [d["imei"] for d in devices] == [IMEI]


async def test_sec_dev_09_device_id_is_one_encoded_path_segment(hass, aioclient_mock):
    from custom_components.eberspaecher.api import _segment

    assert _segment("../x?y") == "..%2Fx%3Fy"


async def test_sec_dev_10_hostile_cloud_payload_is_stored_as_text(hass, aioclient_mock, entry):
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr

    name = "<img src=x onerror=alert(1)>"
    caps = [{"operationMode": "HEATING", "targetRuntime": {"minimumValue": -5, "maximumValue": 10**9}}]
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": name}])
    aioclient_mock.get(f"{BASE_URL}/calls/{IMEI}/heaters",
                       json=[heater(capabilities=caps, heaterErrors=["e"] * 5000)])
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, IMEI)})
    assert device.name == name                              # plain text; the frontend escapes
    low, high, _ = entry.runtime_data.runtime_limits(IMEI, 1)
    assert low >= 1                                         # negative minimum ignored


# --- SEC-DEV-02 bypass review: the controlled device is pinned ----------------------

def list_devices(mock, order, temps=None):
    temps = temps or {}
    mock.clear_requests()
    mock.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    mock.get(f"{BASE_URL}/calls", json=[{"imei": i, "name": i} for i in order])
    for imei in order:
        mock.get(f"{BASE_URL}/calls/{imei}/heaters",
                 json=[heater(lastMeasuredTemperature={"temperature": temps.get(imei, -12)})])
        mock.put(f"{BASE_URL}/calls/{imei}/heaters/1", text="")


async def test_sec_dev_02_reordered_device_list_does_not_move_the_schedule(hass, two_cars):
    entry, m = two_cars
    assert entry.data["schedule_device"] == IMEI
    list_devices(m, [OTHER, IMEI])                      # the cloud now lists the other car first
    entry.runtime_data._calls = []                      # device list cache expired
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert puts_to(m, OTHER) == []
    assert len(puts_to(m, IMEI)) == 1                   # legitimate start still happens


async def test_sec_dev_02_schedule_stops_when_its_device_leaves_the_account(hass, two_cars, caplog):
    entry, m = two_cars
    list_devices(m, [OTHER])
    entry.runtime_data._calls = []
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert puts_to(m, OTHER) == []                      # fails closed: no other car takes over
    assert entry.runtime_data.primary_imei is None


async def test_sec_dev_02_pinned_device_survives_a_restart_with_another_order(hass, two_cars):
    from homeassistant.helpers import entity_registry as er

    entry, m = two_cars
    list_devices(m, [OTHER, IMEI])
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.primary_imei == IMEI
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("switch", DOMAIN, f"{IMEI}_schedule")
    assert reg.async_get_entity_id("switch", DOMAIN, f"{OTHER}_schedule") is None
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert puts_to(m, OTHER) == [] and len(puts_to(m, IMEI)) == 1


async def test_sec_dev_02_existing_install_is_pinned_to_its_current_device(hass, cloud, entry):
    """An entry from before this change gets the device it already controls."""
    assert "schedule_device" not in entry.data
    await setup(hass, entry)
    assert entry.data["schedule_device"] == IMEI
