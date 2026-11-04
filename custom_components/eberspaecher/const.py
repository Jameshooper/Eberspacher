"""Constants for the Eberspächer EasyStart Web integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "eberspaecher"

# The cloud is polled gently: slowly when idle, a bit faster while a run is active.
UPDATE_INTERVAL_IDLE = timedelta(minutes=10)

# Options: how often to check the status while nothing is running. 0 = off (no
# background polling while idle; commands, running heaters and restarts still refresh).
CONF_IDLE_INTERVAL = "idle_interval"
DEFAULT_IDLE_INTERVAL = 10
IDLE_INTERVAL_CHOICES = [0, 5, 10, 15, 30, 60]
UPDATE_INTERVAL_ACTIVE = timedelta(minutes=5)

# How long the list of registered devices is cached between full lookups.
CALLS_CACHE_SECONDS = 6 * 60 * 60

# Delay before re-reading the state after a command, so the cloud can catch up.
REFRESH_DELAY_COMMAND = 15
REFRESH_DELAY_TEMPERATURE = 30

# While the cloud reports a transitional state (e.g. ACTIVATION_REQUESTED), re-read
# every SETTLE_DELAY seconds, at most SETTLE_MAX_READS times in a row, so the
# status catches up without waiting for the next regular poll.
SETTLE_DELAY = 45
SETTLE_MAX_READS = 6
TRANSITIONAL_SUFFIX = "_REQUESTED"

MODE_HEATING = "HEATING"
MODE_VENTILATION = "VENTILATION"
MODE_OFF = "OFF"

STATE_ON = "ON"

DEFAULT_RUNTIME = 30
DEFAULT_MIN_RUNTIME = 10
DEFAULT_MAX_RUNTIME = 120

# Failed updates in a row before a "cloud unreachable" repair issue is raised.
FAILURES_BEFORE_ISSUE = 6

# Schedule options (stored in the config entry options).
CONF_SCHEDULE_ENABLED = "schedule_enabled"
CONF_READY_BY = "ready_by"
CONF_WEEKDAYS = "weekdays"
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NO_HEAT_ABOVE = "no_heat_above"
CONF_MILD_RUNTIME = "mild_runtime"
CONF_COLD_BELOW = "cold_below"
CONF_COLD_RUNTIME = "cold_runtime"
CONF_VERY_COLD_BELOW = "very_cold_below"
CONF_VERY_COLD_RUNTIME = "very_cold_runtime"
CONF_VENT_ENABLED = "vent_enabled"
CONF_VENT_ABOVE = "vent_above"
CONF_VENT_RUNTIME = "vent_runtime"

# Reminder to run the heater now and then (stored in the config entry options).
CONF_REMINDER_ENABLED = "reminder_enabled"
CONF_REMINDER_DAYS = "reminder_days"
DEFAULT_REMINDER_DAYS = 30

# Learning when you leave (stored in the config entry options).
CONF_LEARN_ENABLED = "learn_enabled"
CONF_LEARN_ENTITY = "learn_entity"
CONF_LEARN_STATE = "learn_state"
CONF_LEARN_TRACKER = "learn_tracker"
CONF_LEARN_MARGIN = "learn_margin"
DEFAULT_LEARN_STATE = "CarPlay"
DEFAULT_LEARN_MARGIN = 5

# The second schedule: warm the car before leaving work.
CONF_WORK_ENABLED = "work_enabled"
CONF_WORK_READY_BY = "work_ready_by"
CONF_WORK_WEEKDAYS = "work_weekdays"
CONF_WORK_TEMPERATURE_ENTITY = "work_temperature_entity"
DEFAULT_WORK_READY_BY = "16:30:00"

LEG_HOME = "home"
LEG_WORK = "work"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
