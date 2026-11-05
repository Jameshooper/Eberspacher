"""Security tests: who may control the heater (docs/security-test-plan.md, section B).

The integration has no users or roles of its own; it relies on Home Assistant's entity
permissions. These tests pin that reliance so a future change cannot bypass it.
"""
from __future__ import annotations

from datetime import time

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.eberspaecher.api import BASE_URL
from custom_components.eberspaecher.const import DOMAIN

from .conftest import IMEI, PASSWORD, calls_to, heater
from .test_integration import RUNTIME, SWITCH, setup
from .test_schedule import SCHEDULE_OPTIONS, SENSOR, at

CONTROL_CALLS = [
    (DOMAIN, "start_heating", {"runtime": 30}, SWITCH),
    ("switch", "turn_on", {}, SWITCH),
    ("switch", "turn_off", {}, SWITCH),
    ("switch", "turn_off", {}, "switch.van_schedule"),
    ("number", "set_value", {"value": 60}, RUNTIME),
    ("date", "set_value", {"date": "2030-01-01"}, "date.van_pause_until"),
    ("button", "press", {}, "button.van_request_temperature"),
]


@pytest.fixture
async def read_only_user(hass, hass_owner_user):
    # The first user of a fresh install becomes the owner, so the owner is made first.
    user = await hass.auth.async_create_user("viewer", group_ids=["system-read-only"])
    assert not user.is_admin
    return user


def cloud_commands(mock):
    return [c for c in mock.mock_calls if c[0] in ("PUT", "POST") and "/authenticate" not in str(c[1])]


# --- SEC-AUTHZ-01 -------------------------------------------------------------------

@pytest.mark.parametrize(("domain", "service", "data", "entity"), CONTROL_CALLS)
async def test_sec_authz_01_read_only_user_cannot_control(
    hass, entry, cloud, read_only_user, domain, service, data, entity
):
    await setup(hass, entry)
    cloud.mock_calls.clear()
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            domain, service, {**data, "entity_id": entity}, blocking=True,
            context=Context(user_id=read_only_user.id),
        )
    assert cloud_commands(cloud) == []


async def test_sec_authz_01_user_allowed_one_entity_cannot_control_the_heater(
    hass, entry, cloud, hass_owner_user
):
    from homeassistant.auth.permissions import PolicyPermissions
    from homeassistant.auth.permissions.models import PermissionLookup
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    await setup(hass, entry)
    user = await hass.auth.async_create_user("limited", group_ids=[])
    user._permissions = PolicyPermissions(
        {"entities": {"entity_ids": {"sensor.van_temperature": True}}},
        PermissionLookup(er.async_get(hass), dr.async_get(hass)),
    )
    cloud.mock_calls.clear()
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "start_heating", {"runtime": 30},
                                       target={"entity_id": SWITCH}, blocking=True,
                                       context=Context(user_id=user.id))
    assert cloud_commands(cloud) == []


async def test_sec_authz_01_admin_can_control(hass, entry, cloud, hass_owner_user, hass_admin_user):
    await setup(hass, entry)
    await hass.services.async_call(DOMAIN, "start_heating", {"runtime": 30},
                                   target={"entity_id": SWITCH}, blocking=True,
                                   context=Context(user_id=hass_admin_user.id))
    assert len(calls_to(cloud, "PUT", "/heaters/1")) == 1


async def test_sec_authz_01_service_only_targets_this_integration(hass, entry, cloud):
    """`start_heating` must not act on another integration's switch."""
    await setup(hass, entry)
    hass.states.async_set("switch.something_else", "off")
    cloud.mock_calls.clear()
    await hass.services.async_call(DOMAIN, "start_heating", {"runtime": 30},
                                   target={"entity_id": "switch.something_else"}, blocking=True)
    assert cloud_commands(cloud) == []


# --- SEC-AUTHZ-02 -------------------------------------------------------------------

async def test_sec_authz_02_non_admin_cannot_open_options(
    hass, entry, cloud, hass_client, hass_read_only_access_token
):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "config", {})
    await setup(hass, entry)
    client = await hass_client(hass_read_only_access_token)
    resp = await client.post("/api/config/config_entries/options/flow",
                             json={"handler": entry.entry_id})
    assert resp.status == 401


async def test_sec_authz_02_diagnostics_any_user_can_download_hold_no_secrets(
    hass, entry, cloud, hass_client, hass_read_only_access_token
):
    """Home Assistant's diagnostics download URL has no admin check (2026.2): even a
    read-only user gets the file. So what is in it must be safe for every user."""
    from homeassistant.setup import async_setup_component

    from .conftest import EMAIL

    assert await async_setup_component(hass, "diagnostics", {})
    hass.config_entries.async_update_entry(entry, options={
        "notify_service": "notify.mobile_app_secret_phone",
        "temperature_entity": "sensor.secret_outside",
        "learn_tracker": "device_tracker.secret_phone",
        "learn_entity": "sensor.secret_audio",
        "work_temperature_entity": "sensor.secret_work",
    })
    await setup(hass, entry)
    client = await hass_client(hass_read_only_access_token)
    resp = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
    assert resp.status == 200                     # platform behaviour, documented
    body = await resp.text()
    for secret in (PASSWORD, EMAIL, IMEI, "tok-1", "secret_"):
        assert secret not in body


# --- SEC-AUTHZ-03 -------------------------------------------------------------------

async def test_sec_authz_03_one_account_never_commands_another(hass, aioclient_mock):
    """Two accounts with their own device: each schedule and switch stays with its own."""
    other_imei = "SECOND-ACCOUNT-01"
    m = aioclient_mock

    # The mock cannot tell accounts apart by header, so each device gets its own
    # path and we check which token each command carried.
    m.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    m.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    m.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater()])
    m.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    await hass.config.async_set_time_zone("UTC")
    first = MockConfigEntry(domain=DOMAIN, unique_id="a@example.com",
                            data={"email": "a@example.com", "password": PASSWORD},
                            options=dict(SCHEDULE_OPTIONS))
    first.add_to_hass(hass)
    async_mock_service(hass, "notify", "phone")
    hass.states.async_set(SENSOR, "-12", {"unit_of_measurement": "°C"})
    await setup(hass, first)

    m.clear_requests()
    m.post(f"{BASE_URL}/authenticate", json={"token": "tok-2"})
    m.get(f"{BASE_URL}/calls", json=[{"imei": other_imei, "name": "Car"}])
    m.get(f"{BASE_URL}/calls/{other_imei}/heaters", json=[heater()])
    m.put(f"{BASE_URL}/calls/{other_imei}/heaters/1", text="")
    m.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    second = MockConfigEntry(domain=DOMAIN, unique_id="b@example.com",
                             data={"email": "b@example.com", "password": PASSWORD})
    second.add_to_hass(hass)
    await setup(hass, second)
    m.mock_calls.clear()

    await first.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.car_heating"},
                                   blocking=True)
    puts = [(c[1].path, c[3]["ESCW-Auth-Token"]) for c in m.mock_calls if c[0] == "PUT"]
    assert (f"/escw-application-server/rest/v1/calls/{IMEI}/heaters/1", "tok-1") in puts
    assert (f"/escw-application-server/rest/v1/calls/{other_imei}/heaters/1", "tok-2") in puts
    assert len(puts) == 2


# --- SEC-AUTHZ-04 -------------------------------------------------------------------

@pytest.mark.parametrize("entity", [
    "sensor.van_learned_leave_home_time", "sensor.van_learned_leave_work_time",
])
async def test_sec_authz_04_learned_times_are_diagnostic_and_not_exposed(hass, entry, cloud, entity):
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    await setup(hass, entry)
    reg_entry = er.async_get(hass).async_get(entity)
    assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert not async_should_expose(hass, "conversation", entity)


async def test_sec_authz_02_admin_can_download_diagnostics(hass, entry, cloud, hass_client):
    """The control case: the same request works for an admin, so the 401s above are real."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "diagnostics", {})
    await setup(hass, entry)
    client = await hass_client()
    resp = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
    assert resp.status == 200


async def test_sec_authz_01_default_non_admin_user_can_control(hass, entry, cloud, hass_owner_user):
    """Documents F6: a normal (non-admin) user has full control by Home Assistant's default."""
    await setup(hass, entry)
    user = await hass.auth.async_create_user("normal", group_ids=["system-users"])
    assert not user.is_admin
    await hass.services.async_call(DOMAIN, "start_heating", {"runtime": 30},
                                   target={"entity_id": SWITCH}, blocking=True,
                                   context=Context(user_id=user.id))
    assert len(calls_to(cloud, "PUT", "/heaters/1")) == 1
