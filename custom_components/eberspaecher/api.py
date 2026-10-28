"""Client for the unofficial EasyStart Web (myeberspaecher.com) REST API.

This talks to the same backend the EasyStart Web site uses. It is not a
documented or supported public API and can change without notice.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.myeberspaecher.com/escw-application-server/rest/v1"
TIMEOUT = aiohttp.ClientTimeout(total=30)

HEADER_EMAIL = "ESCW-Auth-Email"
HEADER_PASSWORD = "ESCW-Auth-Password"
HEADER_TOKEN = "ESCW-Auth-Token"


class EberspaecherError(Exception):
    """Base error for the API client."""


class EberspaecherAuthError(EberspaecherError):
    """Login failed or the session was rejected."""


class EberspaecherConnectionError(EberspaecherError):
    """The service could not be reached or returned an unexpected error."""


class EberspaecherApi:
    """Small async client that keeps one session token and re-logs in when needed."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    async def async_login(self) -> None:
        """Log in and store the session token."""
        headers = {
            HEADER_EMAIL: self._email,
            HEADER_PASSWORD: self._password,
            "Accept": "application/json",
        }
        try:
            async with self._session.post(
                f"{BASE_URL}/authenticate", headers=headers, timeout=TIMEOUT
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise EberspaecherAuthError("Invalid email or password")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except EberspaecherError:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EberspaecherConnectionError(f"Login request failed: {err}") from err
        except ValueError as err:
            # A 200 with a non-JSON body, e.g. a maintenance page.
            raise EberspaecherConnectionError(
                "Login response was not valid JSON"
            ) from err

        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise EberspaecherAuthError("Login response did not contain a token")
        self._token = token

    async def _async_ensure_token(self) -> None:
        async with self._login_lock:
            if self._token is None:
                await self.async_login()

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: Any = None,
        retry: bool = True,
    ) -> Any:
        """Send an authenticated request, logging in again once on a 401/403.

        A 401 that survives a fresh login means the credentials are bad (reauth).
        A 403 that survives a fresh login means the login works but this resource
        is not allowed, so it is reported as a connection error, not a bad password.
        """
        await self._async_ensure_token()
        token = self._token
        headers = {HEADER_TOKEN: token or "", "Accept": "application/json"}
        try:
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                headers=headers,
                params=params,
                json=payload,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    # Only drop the token if no other request has replaced it yet.
                    if self._token == token:
                        self._token = None
                    if retry:
                        return await self._async_request(
                            method, path, params=params, payload=payload, retry=False
                        )
                    if resp.status == 403:
                        raise EberspaecherConnectionError(
                            f"{method} {path} was refused (403)"
                        )
                    raise EberspaecherAuthError("Session was rejected")
                resp.raise_for_status()
                text = await resp.text()
        except EberspaecherError:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EberspaecherConnectionError(
                f"{method} {path} failed: {err}"
            ) from err

        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    async def async_get_calls(self) -> list[dict[str, Any]]:
        """Return the devices ("calls") registered to this account."""
        data = await self._async_request(
            "GET",
            "/calls",
            params={"fetchHeater": "NO", "email": "CURRENT", "page": 0, "size": 50},
        )
        return [item for item in _as_list(data) if item.get("imei")]

    async def async_get_heaters(self, imei: str) -> list[dict[str, Any]]:
        """Return the heaters of one device with their current state."""
        data = await self._async_request("GET", f"/calls/{imei}/heaters")
        return _as_list(data)

    async def async_set_heater(
        self, imei: str, address: int, mode: str, runtime: int | None = None
    ) -> None:
        """Start (HEATING / VENTILATION) or stop (OFF) a heater."""
        payload = {
            "runtime": runtime,
            "remainingRuntime": None,
            "targetTemperature": None,
            "operationMode": mode,
            "comfortLevel": None,
            "temperatureLowering": None,
            "altitudeFunction": None,
        }
        await self._async_request(
            "PUT", f"/calls/{imei}/heaters/{address}", payload=payload
        )

    async def async_request_temperature(self, imei: str) -> None:
        """Ask the device to measure and report its temperature."""
        await self._async_request("POST", f"/calls/{imei}/temperature", payload={})


def _as_list(data: Any) -> list[dict[str, Any]]:
    """Accept a bare list or a paged {"content"/"items": [...]} reply."""
    if isinstance(data, dict):
        data = data.get("content") or data.get("items") or []
    if not isinstance(data, list):
        if data:
            _LOGGER.warning("Unexpected reply from EasyStart Web: %s", type(data).__name__)
        return []
    return [item for item in data if isinstance(item, dict)]
