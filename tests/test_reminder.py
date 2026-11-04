"""The reminder to run the heater now and then."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.eberspaecher.const import DOMAIN

from .conftest import EMAIL, PASSWORD, heater
from .test_integration import SWITCH, open_options, set_heater_reply, setup
from .test_schedule import VENT_SWITCH

LAST_HEATED = "sensor.van_last_heated"


@pytest.fixture
async def watched(hass, cloud):
    """A set-up entry with a notify service, plus the notifications it receives."""
    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD},
                        options={"notify_service": "notify.phone"})
    e.add_to_hass(hass)
    notes = async_mock_service(hass, "notify", "phone")
    await setup(hass, e)
    return e, notes


def issues(hass):
    return [i for i in ir.async_get(hass).issues.values() if i.translation_key == "heater_idle"]


async def test_starting_the_heater_sets_last_heated(hass, watched):
    assert hass.states.get(LAST_HEATED).state == "unknown"
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert hass.states.get(LAST_HEATED).state != "unknown"


async def test_ventilation_does_not_count(hass, watched):
    await hass.services.async_call("switch", "turn_on", {"entity_id": VENT_SWITCH}, blocking=True)
    assert hass.states.get(LAST_HEATED).state == "unknown"


async def test_a_run_seen_in_the_cloud_counts(hass, entry, aioclient_mock):
    running = {"state": "ON", "heaterState": "ON", "currentOperation": {
        "remainingRuntime": 20, "operationSettings": {"operationMode": "HEATING"}}}
    set_heater_reply(aioclient_mock, heater(**running))
    await setup(hass, entry)
    assert hass.states.get(LAST_HEATED).state != "unknown"


async def test_ventilation_seen_in_the_cloud_does_not_count(hass, entry, aioclient_mock):
    running = {"state": "ON", "heaterState": "ON", "currentOperation": {
        "remainingRuntime": 20, "operationSettings": {"operationMode": "VENTILATION"}}}
    set_heater_reply(aioclient_mock, heater(**running))
    await setup(hass, entry)
    assert hass.states.get(LAST_HEATED).state == "unknown"


async def test_no_reminder_before_the_days_are_up(hass, watched, freezer):
    entry, notes = watched
    freezer.move_to(dt_util.utcnow() + timedelta(days=29))
    await entry.runtime_data.reminder.async_check()
    assert notes == [] and issues(hass) == []


async def test_reminds_after_30_idle_days_then_weekly(hass, watched, freezer):
    entry, notes = watched
    reminder = entry.runtime_data.reminder
    freezer.move_to(dt_util.utcnow() + timedelta(days=31))
    await reminder.async_check()
    assert len(notes) == 1 and "31 days" in notes[0].data["message"]
    assert len(issues(hass)) == 1
    freezer.move_to(dt_util.utcnow() + timedelta(days=3))
    await reminder.async_check()
    assert len(notes) == 1                      # not again so soon
    freezer.move_to(dt_util.utcnow() + timedelta(days=5))
    await reminder.async_check()
    assert len(notes) == 2                      # a week on


async def test_running_the_heater_clears_the_reminder(hass, watched, freezer):
    entry, notes = watched
    freezer.move_to(dt_util.utcnow() + timedelta(days=40))
    await entry.runtime_data.reminder.async_check()
    assert len(issues(hass)) == 1
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    assert issues(hass) == []
    await entry.runtime_data.reminder.async_check()
    assert len(notes) == 1 and issues(hass) == []


async def test_the_issue_still_shows_without_a_notify_service(hass, entry, cloud, freezer):
    await setup(hass, entry)
    freezer.move_to(dt_util.utcnow() + timedelta(days=31))
    await entry.runtime_data.reminder.async_check()
    assert len(issues(hass)) == 1


async def test_can_be_turned_off(hass, watched, freezer):
    entry, notes = watched
    r = await open_options(hass, entry, "reminder")
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"reminder_enabled": False, "reminder_days": 30})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    freezer.move_to(dt_util.utcnow() + timedelta(days=90))
    await entry.runtime_data.reminder.async_check()
    assert notes == [] and issues(hass) == []


async def test_the_number_of_days_is_a_setting(hass, watched, freezer):
    entry, notes = watched
    r = await open_options(hass, entry, "reminder")
    await hass.config_entries.options.async_configure(
        r["flow_id"], {"reminder_enabled": True, "reminder_days": 10})
    await hass.async_block_till_done()
    assert entry.options["reminder_days"] == 10
    assert entry.options["notify_service"] == "notify.phone"   # other options kept
    freezer.move_to(dt_util.utcnow() + timedelta(days=11))
    await entry.runtime_data.reminder.async_check()
    assert len(notes) == 1


async def test_last_heated_survives_a_reload(hass, watched):
    entry, _ = watched
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    before = hass.states.get(LAST_HEATED).state
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LAST_HEATED).state == before
