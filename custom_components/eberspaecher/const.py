"""Constants for the Eberspächer EasyStart Web integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "eberspaecher"

# The cloud is polled gently: slowly when idle, a bit faster while a run is active.
UPDATE_INTERVAL_IDLE = timedelta(minutes=10)
UPDATE_INTERVAL_ACTIVE = timedelta(minutes=5)

# How long the list of registered devices is cached between full lookups.
CALLS_CACHE_SECONDS = 6 * 60 * 60

# Delay before re-reading the state after a command, so the cloud can catch up.
REFRESH_DELAY_COMMAND = 15
REFRESH_DELAY_TEMPERATURE = 30

MODE_HEATING = "HEATING"
MODE_VENTILATION = "VENTILATION"
MODE_OFF = "OFF"

STATE_ON = "ON"

DEFAULT_RUNTIME = 30
DEFAULT_MIN_RUNTIME = 10
DEFAULT_MAX_RUNTIME = 120
