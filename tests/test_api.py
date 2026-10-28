"""API client tests: auth, retries, error mapping, malformed responses, secret leakage."""
from __future__ import annotations

import asyncio
import logging

import pytest
from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.eberspaecher.api import (
    BASE_URL,
    EberspaecherApi,
    EberspaecherAuthError,
    EberspaecherConnectionError,
)

from .conftest import EMAIL, IMEI, PASSWORD, calls_to


def api(hass):
    return EberspaecherApi(async_get_clientsession(hass), EMAIL, PASSWORD)


async def test_login_sends_credentials_over_https_headers(hass, cloud):
    a = api(hass)
    await a.async_login()
    method, url, _data, headers = cloud.mock_calls[0]
    assert str(url).startswith("https://")
    assert headers["ESCW-Auth-Email"] == EMAIL
    assert headers["ESCW-Auth-Password"] == PASSWORD
    # The password must not end up in the URL / query string.
    assert PASSWORD not in str(url)


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_login_bad_credentials(hass, aioclient_mock, status):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=status)
    with pytest.raises(EberspaecherAuthError):
        await api(hass).async_login()


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_login_server_errors_are_connection_errors(hass, aioclient_mock, status):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=status)
    with pytest.raises(EberspaecherConnectionError):
        await api(hass).async_login()


async def test_login_timeout(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", exc=asyncio.TimeoutError())
    with pytest.raises(EberspaecherConnectionError):
        await api(hass).async_login()


async def test_login_200_without_token(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"nope": 1})
    with pytest.raises(EberspaecherAuthError):
        await api(hass).async_login()


async def test_login_200_non_json_body_is_connection_error(hass, aioclient_mock):
    """An HTML maintenance page with status 200 is a connection problem, not a crash."""
    aioclient_mock.post(f"{BASE_URL}/authenticate", text="<html>maintenance</html>")
    with pytest.raises(EberspaecherConnectionError):
        await api(hass).async_login()


async def test_token_reused_between_requests(hass, cloud):
    a = api(hass)
    await a.async_get_calls()
    await a.async_get_heaters(IMEI)
    assert len(calls_to(cloud, "POST", "/authenticate")) == 1
    assert cloud.mock_calls[1][3]["ESCW-Auth-Token"] == "tok-1"
    # Only the token, never the password, is sent on data requests.
    assert "ESCW-Auth-Password" not in cloud.mock_calls[1][3]


async def test_expired_token_relogin_once_then_succeeds(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    seen = {"n": 0}

    async def flaky(method, url, data):
        seen["n"] += 1
        if seen["n"] == 1:
            return AiohttpClientMockResponse(method, url, status=401)
        return AiohttpClientMockResponse(method, url, json=[{"imei": IMEI}])

    aioclient_mock.get(f"{BASE_URL}/calls", side_effect=flaky)
    calls = await api(hass).async_get_calls()
    assert calls == [{"imei": IMEI}]
    assert len(calls_to(aioclient_mock, "POST", "/authenticate")) == 2


async def test_persistent_401_raises_auth_without_looping(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(f"{BASE_URL}/calls", status=401)
    with pytest.raises(EberspaecherAuthError):
        await api(hass).async_get_calls()
    assert len(calls_to(aioclient_mock, "GET", "/calls")) == 2  # exactly one retry


async def test_403_on_resource_is_not_treated_as_bad_password(hass, aioclient_mock):
    """A 403 that survives a fresh login (valid credentials) must not trigger reauth."""
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", status=403)
    with pytest.raises(EberspaecherConnectionError) as info:
        await api(hass).async_get_heaters(IMEI)
    assert not isinstance(info.value, EberspaecherAuthError)
    assert len(calls_to(aioclient_mock, "POST", "/authenticate")) == 2


async def test_calls_paged_dict_and_filters_junk(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(
        f"{BASE_URL}/calls",
        json={"content": [{"imei": "1"}, {"name": "no imei"}, "junk", None]},
    )
    assert await api(hass).async_get_calls() == [{"imei": "1"}]


async def test_heaters_paged_dict_is_unwrapped(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(
        f"{BASE_URL}/calls/{IMEI}/heaters", json={"content": [{"address": 1}]}
    )
    assert await api(hass).async_get_heaters(IMEI) == [{"address": 1}]


async def test_heaters_unexpected_type_logs_warning(hass, aioclient_mock, caplog):
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "t"})
    aioclient_mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", text="<html>")
    assert await api(hass).async_get_heaters(IMEI) == []
    assert "Unexpected reply" in caplog.text


async def test_set_heater_payload(hass, cloud):
    await api(hass).async_set_heater(IMEI, 1, "HEATING", 40)
    put = calls_to(cloud, "PUT", f"/heaters/1")[0]
    assert put[2]["operationMode"] == "HEATING"
    assert put[2]["runtime"] == 40


async def test_connection_error_messages_do_not_leak_secrets(hass, aioclient_mock, caplog):
    caplog.set_level(logging.DEBUG)
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "SECRET-TOKEN"})
    aioclient_mock.get(f"{BASE_URL}/calls", exc=ClientError("boom"))
    with pytest.raises(EberspaecherConnectionError) as info:
        await api(hass).async_get_calls()
    for text in (str(info.value), caplog.text):
        assert PASSWORD not in text
        assert "SECRET-TOKEN" not in text


async def test_login_http_error_message_has_no_password(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/authenticate", status=500)
    with pytest.raises(EberspaecherConnectionError) as info:
        await api(hass).async_login()
    assert PASSWORD not in str(info.value)
    assert EMAIL not in str(info.value)


async def test_concurrent_requests_login_only_once(hass, cloud):
    a = api(hass)
    await asyncio.gather(*(a.async_get_heaters(IMEI) for _ in range(5)))
    assert len(calls_to(cloud, "POST", "/authenticate")) == 1
