"""Security tests: secrets and identifiers (docs/security-test-plan.md, sections A, E, F)."""
from __future__ import annotations

import json
import logging
from datetime import time

import pytest
from aiohttp import ClientError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.eberspaecher.api import (
    BASE_URL,
    EberspaecherApi,
    EberspaecherConnectionError,
)
from custom_components.eberspaecher.const import DOMAIN, FAILURES_BEFORE_ISSUE
from custom_components.eberspaecher.diagnostics import async_get_config_entry_diagnostics

from .conftest import EMAIL, IMEI, PASSWORD, calls_to, heater
from .test_integration import SWITCH, setup
from .test_schedule import at, scheduled  # noqa: F401

TOKEN = "tok-1"
FAILURES = {
    "http 500": {"status": 500},
    "timeout": {"exc": TimeoutError()},
    "client error": {"exc": ClientError("boom")},
}


def fail_commands(mock, how):
    mock.clear_requests()
    mock.post(f"{BASE_URL}/authenticate", json={"token": TOKEN})
    mock.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", **how)
    mock.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", **how)
    mock.post(f"{BASE_URL}/calls/{IMEI}/temperature", **how)


# --- SEC-AUTH-01 ----------------------------------------------------------------------

async def test_sec_auth_01_password_only_in_the_login_header(hass, entry, cloud):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    for method, url, body, headers in cloud.mock_calls:
        assert url.scheme == "https" and url.host == "www.myeberspaecher.com"
        assert PASSWORD not in str(url) and PASSWORD not in json.dumps(body)
        password_headers = [k for k, v in (headers or {}).items() if v == PASSWORD]
        if url.path.endswith("/authenticate"):
            assert password_headers == ["ESCW-Auth-Password"]
        else:
            assert password_headers == []


# --- SEC-AUTH-02 / SEC-INFO-02 ----------------------------------------------------------

@pytest.mark.parametrize("how", FAILURES.values(), ids=FAILURES.keys())
async def test_sec_auth_02_no_secret_or_device_id_in_any_sink(hass, scheduled, cloud, caplog, how):
    """Errors reach users (toasts, last result), phones (push) and logs: keep them clean."""
    from homeassistant.exceptions import HomeAssistantError

    caplog.set_level(logging.DEBUG)
    entry, notes = scheduled
    fail_commands(cloud, how)
    sinks = []
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    sinks.append(str(err.value))
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press",
                                       {"entity_id": "button.van_request_temperature"},
                                       blocking=True)
    sinks.append(str(err.value))
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    sinks += [entry.runtime_data.scheduler.last_result, json.dumps([n.data for n in notes])]
    sinks.append(hass.states.get("switch.van_schedule").attributes["last_result"])
    for text in sinks:
        assert PASSWORD not in text and TOKEN not in text and EMAIL not in text
        assert IMEI not in text, text
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text


async def test_sec_info_02_repair_issue_has_no_device_id(hass, entry, cloud):
    await setup(hass, entry)
    fail_commands(cloud, {"status": 500})
    for _ in range(FAILURES_BEFORE_ISSUE):
        await entry.runtime_data.async_refresh()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"cloud_unreachable_{entry.entry_id}")
    assert issue is not None
    assert IMEI not in json.dumps(issue.translation_placeholders)
    assert "…0001" in issue.translation_placeholders["error"]       # still recognisable


async def test_sec_info_03_debug_logs_shorten_the_device_id(hass, entry, cloud, caplog):
    caplog.set_level(logging.DEBUG)
    await setup(hass, entry)
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text
    assert f"Raw heaters for {IMEI}" not in caplog.text


# --- SEC-AUTH-03 / SEC-AUTH-04 -----------------------------------------------------------

async def test_sec_auth_03_bad_password_is_not_retried_in_the_background(
    hass, entry, aioclient_mock, freezer
):
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    aioclient_mock.post(f"{BASE_URL}/authenticate", status=401)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first = len(calls_to(aioclient_mock, "POST", "/authenticate"))
    for _ in range(6):
        freezer.tick(timedelta(minutes=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(calls_to(aioclient_mock, "POST", "/authenticate")) == first == 1
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_sec_auth_04_refused_requests_do_not_resend_the_password(hass, entry, cloud):
    await setup(hass, entry)
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/authenticate", json={"token": TOKEN})
    cloud.post(f"{BASE_URL}/calls/{IMEI}/temperature", status=403)
    from homeassistant.exceptions import HomeAssistantError

    for _ in range(5):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("button", "press",
                                           {"entity_id": "button.van_request_temperature"},
                                           blocking=True)
    assert calls_to(cloud, "POST", "/authenticate") == []


# --- SEC-AUTH-05 ------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    {"json": {"token": ""}}, {"json": {"token": None}}, {"json": {"token": 123}},
    {"json": []}, {"json": {"token": ["x"]}}, {"text": "<html>maintenance</html>"},
])
async def test_sec_auth_05_unusable_login_replies_never_authenticate(hass, aioclient_mock, reply):
    aioclient_mock.post(f"{BASE_URL}/authenticate", **reply)
    aioclient_mock.get(f"{BASE_URL}/calls", json=[])
    api = EberspaecherApi(async_get_clientsession(hass), EMAIL, PASSWORD)
    with pytest.raises(Exception):
        await api.async_get_calls()
    assert calls_to(aioclient_mock, "GET", "/calls") == []


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_sec_auth_05_redirects_are_not_followed(hass, aioclient_mock, status):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=status,
                        headers={"Location": "https://attacker.example/steal"})
    api = EberspaecherApi(async_get_clientsession(hass), EMAIL, PASSWORD)
    with pytest.raises(EberspaecherConnectionError):
        await api.async_login()
    hosts = {c[1].host for c in aioclient_mock.mock_calls}
    assert hosts == {"www.myeberspaecher.com"}


async def test_sec_auth_05_requests_ask_not_to_follow_redirects(hass, cloud, monkeypatch):
    seen = []
    session = async_get_clientsession(hass)
    real_request, real_post = session.request, session.post

    def request(*args, **kwargs):
        seen.append(kwargs.get("allow_redirects"))
        return real_request(*args, **kwargs)

    def post(*args, **kwargs):
        seen.append(kwargs.get("allow_redirects"))
        return real_post(*args, **kwargs)

    monkeypatch.setattr(session, "request", request)
    monkeypatch.setattr(session, "post", post)
    api = EberspaecherApi(session, EMAIL, PASSWORD)
    await api.async_get_calls()
    assert seen and all(value is False for value in seen)


# --- SEC-AUTH-06 / SEC-AUTH-07 -----------------------------------------------------------

async def test_sec_auth_06_reauth_cannot_switch_account(hass, cloud, entry):
    import voluptuous as vol

    await setup(hass, entry)
    result = await entry.start_reauth_flow(hass)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "new", "email": "attacker@example.com"})
    assert entry.data["email"] == EMAIL


async def test_sec_auth_07_duplicate_account_with_whitespace_is_refused(hass, cloud, entry):
    await setup(hass, entry)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": f"  {EMAIL.upper()} ", "password": PASSWORD})
    assert result["type"] == "abort" and result["reason"] == "already_configured"


# --- SEC-SESS-01 / SEC-INFO-05 -----------------------------------------------------------

async def test_sec_sess_01_token_is_never_stored_and_password_only_in_the_entry(
    hass, entry, cloud, hass_storage
):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    for key, value in hass_storage.items():
        text = json.dumps(value)
        assert TOKEN not in text, key
        if key.startswith("eberspaecher."):
            assert PASSWORD not in text, key


# --- SEC-API-01 -------------------------------------------------------------------------

@pytest.mark.parametrize("runtime", [0, -1, 9, 121, 601, 10**9, "abc", None, True, False, [30]])
async def test_sec_api_01_bad_runtime_never_reaches_the_heater(hass, entry, cloud, runtime):
    import voluptuous as vol
    from homeassistant.exceptions import ServiceValidationError

    await setup(hass, entry)
    cloud.mock_calls.clear()
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(DOMAIN, "start_heating", {"runtime": runtime},
                                       target={"entity_id": SWITCH}, blocking=True)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


@pytest.mark.parametrize("mode", ["OFF", "off", "heating", "", None, ["HEATING"]])
async def test_sec_api_01_bad_mode_never_reaches_the_heater(hass, entry, cloud, mode):
    import voluptuous as vol

    await setup(hass, entry)
    cloud.mock_calls.clear()
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "start_heating", {"runtime": 30, "mode": mode},
                                       target={"entity_id": SWITCH}, blocking=True)
    assert calls_to(cloud, "PUT", "/heaters/1") == []


# --- SEC-API-03 / SEC-API-04 -------------------------------------------------------------

@pytest.mark.parametrize(("step", "data"), [
    ("schedule", {"schedule_enabled": True, "ready_by": "08:00:00", "weekdays": ["mon"],
                  "learn_tracker": "device_tracker.evil"}),
    ("heating_bands", {"no_heat_above": 10, "mild_runtime": 40, "cold_below": 0,
                       "cold_runtime": 70, "very_cold_below": -10, "very_cold_runtime": 100,
                       "notify_service": "script.evil"}),
    ("learning", {"learn_enabled": False, "learn_state": "CarPlay", "learn_margin": 5,
                  "schedule_enabled": True}),
])
async def test_sec_api_03_options_reject_extra_fields(hass, entry, cloud, step, data):
    import voluptuous as vol

    from .test_integration import open_options

    await setup(hass, entry)
    flow = await open_options(hass, entry, step)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(flow["flow_id"], data)


@pytest.mark.parametrize("options", [
    {"schedule_enabled": True, "ready_by": "25:99", "weekdays": ["mon"]},
    {"schedule_enabled": True, "weekdays": "mon"},
    {"schedule_enabled": True, "very_cold_runtime": "abc"},
    {"learn_enabled": True, "learn_margin": -100, "learn_entity": "sensor.a",
     "learn_tracker": "device_tracker.b"},
    {"schedule_enabled": True, "notify_service": 123},
])
async def test_sec_api_04_tampered_options_load_safely(hass, cloud, options):
    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    e = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL,
                        data={"email": EMAIL, "password": PASSWORD}, options=options)
    e.add_to_hass(hass)
    await setup(hass, e)
    assert e.state is ConfigEntryState.LOADED


# --- SEC-API-05 -------------------------------------------------------------------------

@pytest.mark.parametrize("target", [
    "script.anything", "lock.unlock", "homeassistant.restart", "shell_command.x",
])
async def test_sec_api_05_notify_setting_never_calls_other_services(hass, scheduled, cloud, target):
    entry, _ = scheduled
    domain, name = target.split(".")
    called = async_mock_service(hass, domain, name)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "notify_service": target})
    await hass.async_block_till_done()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert called == []


@pytest.mark.parametrize("target", ["notify.mobile_app_phone", "mobile_app_phone"])
async def test_sec_api_05_notify_services_still_work(hass, scheduled, cloud, target):
    entry, _ = scheduled
    called = async_mock_service(hass, "notify", "mobile_app_phone")
    hass.config_entries.async_update_entry(entry, options={**entry.options, "notify_service": target})
    await hass.async_block_till_done()
    await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
    assert len(called) == 1


async def test_sec_api_05_settings_refuse_a_non_notify_service(hass, entry, cloud):
    from .test_integration import open_options

    await setup(hass, entry)
    flow = await open_options(hass, entry, "schedule")
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        "schedule_enabled": True, "ready_by": "08:00:00", "weekdays": ["mon"],
        "notify_service": "script.unlock_front_door"})
    assert result["errors"] == {"base": "notify_not_notify"}


# --- SEC-INFO-01 ------------------------------------------------------------------------

async def test_sec_info_01_diagnostics_hold_no_identifiers(hass, aioclient_mock, entry):
    extra = {"serialNumber": "SER-123", "iccid": "8944-ICCID", "imsi": "IMSI-1",
             "msisdn": "+35840000", "phoneNumber": "+35841111", "ownerEmail": "own@x.y",
             "name": "My secret car", "description": "Plate ABC-123"}
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": TOKEN})
    aioclient_mock.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    aioclient_mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater(**extra)])
    hass.config_entries.async_update_entry(entry, options={
        "notify_service": "notify.mobile_app_secret", "temperature_entity": "sensor.secret_t",
        "learn_entity": "sensor.secret_audio", "learn_tracker": "device_tracker.secret",
        "work_temperature_entity": "sensor.secret_work"})
    await setup(hass, entry)
    text = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for value in (PASSWORD, EMAIL, IMEI, TOKEN, "secret", *extra.values()):
        assert value not in text, value


# --- SEC-INFO-04 ------------------------------------------------------------------------

async def test_sec_info_04_removal_deletes_both_stores_and_old_samples_are_pruned(
    hass, entry, cloud, hass_storage, freezer
):
    from datetime import datetime, timezone

    freezer.move_to(datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
    await setup(hass, entry)
    learner = entry.runtime_data.learner
    learner._samples["home"] = {"2026-01-01": 480, "2026-10-28": 480}   # one is > 120 days
    assert "2026-01-01" not in learner._data()["samples"]["home"]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    keys = {f"eberspaecher.{entry.entry_id}.departures", f"eberspaecher.{entry.entry_id}.reminder"}
    assert keys & set(hass_storage)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not keys & set(hass_storage)


# --- SEC-INFO-02 / SEC-INFO-01 bypass review --------------------------------------------

@pytest.mark.parametrize("exc", [
    __import__("aiohttp").InvalidURL(f"{BASE_URL}/calls/{IMEI}/heaters/1"),
    __import__("aiohttp").ClientPayloadError(f"payload for {BASE_URL}/calls/{IMEI}/heaters/1"),
    __import__("aiohttp").ServerDisconnectedError(),
])
async def test_sec_info_02_other_client_errors_do_not_carry_the_device_id(hass, aioclient_mock, exc):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": TOKEN})
    aioclient_mock.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", exc=exc)
    api = EberspaecherApi(async_get_clientsession(hass), EMAIL, PASSWORD)
    with pytest.raises(EberspaecherConnectionError) as info:
        await api.async_set_heater(IMEI, 1, "HEATING", 30)
    assert IMEI not in str(info.value)
    assert "…0001" in str(info.value)                   # still recognisable


async def test_sec_info_01_identifiers_are_redacted_in_any_spelling(hass, aioclient_mock, entry):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": TOKEN})
    aioclient_mock.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    odd = {"IMEI": "IMEI-UPPER", "SerialNumber": "SER-X", "ICCID": "ICC-X",
           "phone_number": "+3584", "Owner_Email": "o@x.y"}
    aioclient_mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater(**odd)])
    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag)
    for value in odd.values():
        assert value not in text, value
    heater_data = diag["devices"]["device_1"]["heaters"][1]
    assert heater_data["state"] == "OFF"                # useful, non-identifying data is kept
    assert diag["entry"]["schedule_device"] == "**REDACTED**"
