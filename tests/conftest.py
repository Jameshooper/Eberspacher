"""Shared fixtures: a fake EasyStart Web cloud."""
from __future__ import annotations

import copy

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eberspaecher.api import BASE_URL
from custom_components.eberspaecher.const import DOMAIN

EMAIL = "user@example.com"
PASSWORD = "not-a-real-password"
IMEI = "TEST-IMEI-0001"

HEATER_OFF = {
    "address": 1,
    "state": "OFF",
    "heaterState": "OFF",
    "lastMeasuredTemperature": None,
    "currentOperation": None,
    "heaterErrors": [],
    "capabilities": [
        {
            "operationMode": "HEATING",
            "targetRuntime": {"minimumValue": 10, "maximumValue": 120, "defaultValue": 30},
        },
        {"operationMode": "VENTILATION"},
    ],
}


def heater(**over):
    h = copy.deepcopy(HEATER_OFF)
    h.update(over)
    return h


@pytest.fixture(scope="session", autouse=True)
def _prewarm_aiohttp_session():
    """Create and close an aiohttp connector once before all tests.

    On Python 3.12 with the aiohttp CP312 wheel, the first connector close
    while an event loop is running spawns a daemon thread named
    _run_safe_shutdown_loop.  By triggering this in a session-scoped fixture
    we ensure the thread exists before any test's ``hass`` fixture records its
    thread baseline, so it is never counted as a new thread.
    """
    import asyncio
    import aiohttp

    async def _prewarm():
        connector = aiohttp.TCPConnector()
        await connector.close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_prewarm())
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry(hass):
    e = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        title=EMAIL,
        data={"email": EMAIL, "password": PASSWORD},
    )
    e.add_to_hass(hass)
    return e


@pytest.fixture
def cloud(aioclient_mock):
    """Default healthy cloud with one device and one heater."""
    aioclient_mock.post(f"{BASE_URL}/authenticate", json={"token": "tok-1"})
    aioclient_mock.get(f"{BASE_URL}/calls", json=[{"imei": IMEI, "name": "Van"}])
    aioclient_mock.get(f"{BASE_URL}/calls/{IMEI}/heaters", json=[heater()])
    aioclient_mock.put(f"{BASE_URL}/calls/{IMEI}/heaters/1", text="")
    aioclient_mock.post(f"{BASE_URL}/calls/{IMEI}/temperature", text="")
    return aioclient_mock


def calls_to(mock, method, suffix):
    return [c for c in mock.mock_calls if c[0] == method and c[1].path.endswith(suffix)]
