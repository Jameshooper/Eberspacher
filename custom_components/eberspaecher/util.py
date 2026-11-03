"""Small helpers shared by the platforms and the schedule."""

from __future__ import annotations

from typing import Any


def parse_temperature(value: Any) -> float | None:
    """A temperature as a float from the API's reading or a state string, else None."""
    # The API returns a dict: {'temperature': 13, 'temperatureUnit': 'DEGREE_CELSIUS'}
    if isinstance(value, dict):
        value = value.get("temperature")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
