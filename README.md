# Eberspächer EasyStart Web for Home Assistant

Control an Eberspächer parking heater that is registered with
[EasyStart Web](https://www.myeberspaecher.com) from Home Assistant: start it for a set
time, stop it, and see whether it is running.

> **Unofficial.** This is not made, endorsed or supported by Eberspächer. It talks to
> the same private web API the EasyStart Web site uses. That API is undocumented and
> can change or stop working at any time. Check Eberspächer's terms of use before
> relying on it. Use at your own risk, and never rely on it for safety-critical heating.

**Status: early. Requires Home Assistant 2025.3 or newer. Written against one device (a single water heater).** Other
models and multi-heater units may behave differently. Bug reports with the
Home Assistant log are welcome.

## Install (HACS)

[![Open your Home Assistant instance and open this repository in the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Jameshooper&repository=Eberspacher&category=integration)

1. In HACS, search for **Eberspächer EasyStart Web** and download it.
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → Eberspächer EasyStart Web**,
   then sign in with your myeberspaecher.com email and password.

If it does not show up in the HACS search yet, add it as a custom repository instead:
HACS → three-dot menu → **Custom repositories** → add this repository, category
**Integration**, then continue from step 1.

Manual install: copy `custom_components/eberspaecher` into your Home Assistant
`custom_components` folder and restart.

Your email and password are stored in your own Home Assistant, as with other cloud
integrations. They are only sent to myeberspaecher.com.

## What you get

Per device (found automatically) and per heater:

| Entity | What it does |
|---|---|
| `switch` Heating | On starts the heater for the **Run time**; off stops it |
| `switch` Ventilation | Same, but runs ventilation (fan only). Only created if the heater lists ventilation |
| `switch` Schedule | Turns the automatic start schedule on or off (once per device). Its attributes show today's ready-by and start times (the learned ones when there are any) and the last result, also for leaving work |
| `number` Run time | Minutes the switch runs the heater (the heater's own min/max apply, 10–120 on the unit this was built on). Remembered across restarts |
| `date` Pause until | The schedules do nothing up to and including this day, for example while the car is not used. Remembered across restarts (once per device) |
| `sensor` Remaining run time | Minutes left in the current run |
| `sensor` Status | Raw cloud state, e.g. `OFF`, `ACTIVATION_REQUESTED` (disabled by default) |
| `sensor` Temperature | Last temperature the device reported |
| `sensor` Last heated | When Home Assistant last saw the heater run in heating mode (once per device) |
| `sensor` Learned leave-home / leave-work time | The ready-by time learned for today, and for each weekday (diagnostic, once per device) |
| `button` Request temperature | Asks the device for a fresh reading |

**Request temperature is manual on purpose.** It wakes the device, so it is not polled.
The Temperature sensor stays empty until you press it.

### Service `eberspaecher.start_heating`

Start a heater for a chosen time without changing the Run time number. The run time must be
within the heater's own limits, and the mode must be one the heater supports.

```yaml
action: eberspaecher.start_heating
target:
  entity_id: switch.my_heater_heating
data:
  runtime: 40        # minutes, optional
  mode: HEATING      # or VENTILATION, optional (defaults to the switch's own mode)
```

## Polling and rate limits

| When | How often |
|---|---|
| Nothing running | Every 10 minutes by default, or your choice (see below) |
| Heater running | Every 5 minutes |
| After a start or stop from Home Assistant | 15 seconds later |
| Cloud says starting or stopping (e.g. `ACTIVATION_REQUESTED`) | Every 45 seconds, at most 6 times in a row |

To change the idle check, go to **Settings → Devices & services → Eberspächer EasyStart
Web → Configure** and pick every 5, 10, 15, 30 or 60 minutes, or **Off**. With Off there
is no background polling while idle, so a start made from the EasyStart app, website or
the heater's own timer only shows up after the next command from Home Assistant, a
restart, or a manual `homeassistant.update_entity`.

The login token is reused and refreshed only when the service rejects it. The list of
devices is cached for 6 hours. There is no documented rate limit, so the integration
deliberately stays gentle.

## Automatic start schedule

The integration can start the heater by itself so the car is ready at a set time. The
colder it is, the earlier it starts.

The **Schedule** switch on the device page turns it on or off. Everything else is set under
**Settings → Devices & services → Eberspächer EasyStart Web → Configure → Schedule**: the days,
the ready-by time, the outside temperature sensor and the notification service (for example
`notify.mobile_app_your_phone`). **Heating bands** holds the temperatures and run times, and **Summer ventilation** the hot-weather rule.
Settings that contradict each other are refused with a message, for example a Cold
temperature above No heating above.

Defaults, ready by 08:10 on Monday to Friday:

| Outside temperature | Run time | Starts at |
|---|---|---|
| Above 10 °C | Not started | |
| Above 0 up to 10 °C | 40 min | 07:30 |
| Above -10 up to 0 °C | 70 min | 07:00 |
| -10 °C or colder | 100 min | 06:30 |

The integration wakes at each of those start times, reads the temperature and starts the
heater only if the temperature belongs to that start time's band. It starts at most once a
day and leaves a heater that is already running alone. On a day that needs no heating you
get a "not needed" message at the last start time.

- **Temperature source:** the sensor you choose (°C or °F). A reading that cannot be real
  (not a number, or outside -60 to 70 °C) counts as no reading. If the sensor is unavailable, the
  heater's last reading is used (press **Request temperature** to refresh it). With no
  reading at all, the heater is not started and you get a "not started" message at the
  last start time.
- **Pausing:** set the **Pause until** date for a few days without the car (both schedules skip up to and including that day and resume the next), or use the **Schedule** switch to turn it off until you turn it back on.
- **Notifications:** a message when the heater starts, when starting fails, and when it is
  not needed. Only a notify service is accepted (for example `notify.mobile_app_your_phone`).
  Leave it empty for none.
- **Several devices on one account:** the schedule only controls the device that has the
  **Schedule** switch, and only uses that device's reading. That device is chosen when the
  integration is first set up (the first one the account lists) and kept, even if the account
  later lists its devices in another order. If it leaves the account, the schedule does nothing.
- **The longest run time must fit** between midnight and the ready-by time.

### Summer ventilation

If your heater supports ventilation, a **Ventilation** switch appears next to Heating. It
runs the fan without heating, using the same **Run time** number (kept within the
ventilation limits the heater reports). In the Configure menu, **Summer ventilation**
turns on a rule: when the temperature is at or above a chosen value (25 °C by
default), the schedule runs ventilation for the chosen time (20 minutes by default) so the
car is cooler when you get in. It uses the same ready-by time and days. What ventilation
does depends on the heater model, so try it by hand first.

### Learned departure times

Instead of one fixed ready-by time, the integration can learn when you really get in the
car and have it ready by then, separately for each weekday. Turn it on under **Configure →
Learn departure times** and choose:

- a **departure sensor** and the **state** that means you got in. For an iPhone with the
  Home Assistant app, the *Audio Output* sensor switches to `CarPlay` when the phone
  connects to the car;
- the **phone location** (its device tracker or person).

The first connection of a morning with your phone at home, and of an afternoon (from 13:00)
after at least three hours away from home, is kept for each day. A shorter outing, such as
the way back from the shops on a day off, does not count as leaving work. Today's departure
is only used from the next week on, so it never moves today's plan. History Home Assistant already holds is read when you switch it on, so it does not
start from nothing. Where that history reaches, it replaces what was kept before. For a weekday with at least 3 recent matching days, the car is made
ready by the early end of those departures (the early quarter) minus the margin you set
(5 minutes by default), rounded down to 5 minutes and never more than two hours away from
the fixed time. With fewer days, the fixed time from the Schedule settings is used. The
evening before, at 20:00, you get a message when the learned time differs from the fixed
one, unless that day is paused. Everything stays inside Home Assistant, and removing the
integration deletes what it learned.

With a phone location set, the morning schedule also does nothing on a day your phone is not at
home when a start is due.

### Leaving work

**Configure → Leaving work** adds a second schedule that warms the car before you leave
work. It uses the same heating bands, notification service and Schedule switch (turning the
switch off pauses both), with its own days, ready-by time (16:30 by default) and, if you
turn learning on, its own learned time.

- **Temperature:** by default the car's own. The integration asks the car for a fresh
  reading and waits up to a minute for the answer, which works in an underground car park.
  An older reading is never used: if the car does not answer, the heater is not started. A
  reading from the last 25 minutes is used again, so the device is woken at most three times
  a day. In the same screen you can
  choose a temperature sensor or weather entity for work instead, which is then used
  without asking the car. Your outdoor sensor at home is never used here, and with no
  reading the heater is not started and you get a message.
- **No work location needed, but the phone's location is.** Choose your phone under
  **Learn departure times** (learning itself can stay off). Nothing starts while your phone is
  at home or while its location is unknown, and without a phone location this schedule
  stays off.
- Notifications from this schedule start with "At work:".

### Prefer your own automation?

The schedule is optional. Leave it off and use `eberspaecher.start_heating` from your own
automations, for example:

```yaml
alias: Car pre-heat when very cold
triggers:
  - trigger: time
    at: "06:30:00"
conditions:
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
  - condition: numeric_state
    entity_id: sensor.outdoor_temperature
    below: -10
actions:
  - action: eberspaecher.start_heating
    target: { entity_id: switch.my_heater_heating }
    data: { runtime: 100 }
```

## Idle reminder

A heater that sits unused for months, such as over the summer, should be run now and then
so the fuel lines and pump stay in working order. Home Assistant remembers when the heater
last ran in heating mode (the **Last heated** sensor; ventilation does not count). Once it
has been idle for 30 days you get a message through the notification service from the
Schedule settings, and a notice under **Settings → Repairs**. The message repeats weekly
until the heater runs, then the notice goes away.

Turn it off or change the number of days under **Configure → Idle reminder**. It counts from
the day the integration first saw the heater, so there is no false alarm right after an update.
It only sees runs that Home Assistant started, or that were running at a status check, so a
short run started from the app and finished between checks may not be noticed.

## Diagnostics and repairs

- **Download diagnostics** on the integration page gives a file for bug reports. Your
  email, password, device ID, the entities you chose and the device's identifiers are removed
  from it. Home Assistant lets any signed-in user download it, so nothing personal is kept in it.
- Error messages show only the last four characters of the device ID.
- If the cloud cannot be reached for six checks in a row, a notice appears under
  **Settings → Repairs**. It goes away by itself when the connection works again.

## Languages

English, German, Swedish and Finnish. Home Assistant uses the language of your profile for
the settings menus, entity names and notices. The notifications the integration sends (schedule and
reminder) and the schedule's last result use the same language.

## Known limits

- With a binary sensor as the departure sensor, the state is `on` (or `off`), not `CarPlay`.
- Learning needs your phone's location and a departure sensor. It cannot know about days
  you leave at an unusual time for a reason it cannot see.

- Only tested against one device. The device list and heater fields are read defensively,
  but other models may expose different fields.
- Starting or stopping from the EasyStart Web site or app is picked up at the next poll,
  not instantly.
- No live updates: it is cloud polling, so there is a delay of up to a few minutes
  unless Home Assistant sent the command itself.
