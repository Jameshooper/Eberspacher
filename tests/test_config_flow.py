"""Config flow and reauth tests."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.eberspaecher.api import BASE_URL
from custom_components.eberspaecher.const import DOMAIN

from .conftest import EMAIL, IMEI, PASSWORD


async def start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_success_trims_email(hass, cloud):
    r = await start(hass)
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"email": f"  {EMAIL}  ", "password": PASSWORD}
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"] == {"email": EMAIL, "password": PASSWORD}
    assert r["result"].unique_id == EMAIL


@pytest.mark.parametrize(
    ("setup_mock", "error"),
    [
        (lambda m: m.post(f"{BASE_URL}/authenticate", status=401), "invalid_auth"),
        (lambda m: m.post(f"{BASE_URL}/authenticate", status=503), "cannot_connect"),
        (lambda m: m.post(f"{BASE_URL}/authenticate", text="<html>"), "cannot_connect"),
        (lambda m: (m.post(f"{BASE_URL}/authenticate", json={"token": "t"}),
                    m.get(f"{BASE_URL}/calls", json=[])), "no_devices"),
    ],
)
async def test_user_flow_errors(hass, aioclient_mock, setup_mock, error):
    setup_mock(aioclient_mock)
    r = await start(hass)
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"email": EMAIL, "password": "x"}
    )
    assert r["type"] is FlowResultType.FORM
    assert r["errors"] == {"base": error}


async def test_duplicate_account_case_insensitive(hass, cloud, entry):
    r = await start(hass)
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"email": EMAIL.upper(), "password": PASSWORD}
    )
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "already_configured"


async def test_reauth_updates_password(hass, cloud, entry):
    r = await entry.start_reauth_flow(hass)
    assert r["step_id"] == "reauth_confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"password": "new"})
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"
    # async_update_reload_and_abort schedules a reload as a background task; drain it
    # here so the new coordinator is fully set up before HA teardown fires the stop
    # event — otherwise the coordinator misses the event and its timer lingers.
    await hass.async_block_till_done()


async def test_reauth_wrong_password_shows_error(hass, aioclient_mock, entry):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=401)
    r = await entry.start_reauth_flow(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"password": "bad"})
    assert r["errors"] == {"base": "invalid_auth"}
    assert entry.data["password"] == PASSWORD


async def test_empty_password_is_sent_to_cloud(hass, aioclient_mock):
    """Minor: no client-side check for an empty password."""
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=401)
    r = await start(hass)
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"email": EMAIL, "password": ""}
    )
    assert r["errors"] == {"base": "invalid_auth"}
    assert len(aioclient_mock.mock_calls) == 1
