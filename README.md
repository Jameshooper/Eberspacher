# Eberspächer EasyStart Web for Home Assistant

Control an Eberspächer parking heater that is registered with
[EasyStart Web](https://www.myeberspaecher.com) from Home Assistant: start it for a set
time, stop it, and see whether it is running.

> **Unofficial.** This is not made, endorsed or supported by Eberspächer. It talks to
> the same private web API the EasyStart Web site uses. That API is undocumented and
> can change or stop working at any time. Check Eberspächer's terms of use before
> relying on it. Use at your own risk, and never rely on it for safety-critical heating.

**Status: early (0.1.4). Requires Home Assistant 2025.3 or newer. Written against one device (a single water heater).** Other
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
| `switch` Schedule | Turns the automatic start schedule on or off (once per device) |
| `number` Run time | Minutes the switch runs the heater (the heater's own min/max apply, 10–120 on the unit this was built on). Remembered across restarts |
| `sensor` Remaining run time | Minutes left in the current run |
| `sensor` Status | Raw cloud state, e.g. `OFF`, `ACTIVATION_REQUESTED` (disabled by default) |
| `sensor` Temperature | Last temperature the device reported |
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
colder it is, the earlier it starts. Set it up under **Settings → Devices & services →
Eberspächer EasyStart Web → Configure**:

| Menu item | What you set |
|---|---|
| Schedule | On/off, the **ready-by** time, the days, an outside temperature sensor and an optional notification service (for example `notify.mobile_app_your_phone`) |
| Heating bands | The temperatures and run time for each band |
| Summer ventilation | Optional: ventilate instead of heat when it is warm |

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

- **Temperature source:** the sensor you choose (°C or °F). If it is unavailable, the
  heater's last reading is used (press **Request temperature** to refresh it). With no
  reading at all, the heater is not started and you get a "not started" message at the
  last start time.
- **Pausing:** use the **Schedule** switch, for example on holiday.
- **Notifications:** a message when the heater starts, when starting fails, and when it is
  not needed. Leave the service empty for none.
- **The longest run time must fit** between midnight and the ready-by time.

### Summer ventilation

If your heater supports ventilation, a **Ventilation** switch appears next to Heating. It
runs the fan without heating, using the same **Run time** number (kept within the
ventilation limits the heater reports). In the schedule menu you can turn on
**Summer ventilation**: when the temperature is at or above a chosen value (25 °C by
default), the schedule runs ventilation for the chosen time (20 minutes by default) so the
car is cooler when you get in. It uses the same ready-by time and days. What ventilation
does depends on the heater model, so try it by hand first.

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

## Diagnostics and repairs

- **Download diagnostics** on the integration page gives a file for bug reports. Your
  email, password and device ID are removed from it.
- If the cloud cannot be reached for six checks in a row, a notice appears under
  **Settings → Repairs**. It goes away by itself when the connection works again.

## Known limits

- Only tested against one device. The device list and heater fields are read defensively,
  but other models may expose different fields.
- Starting or stopping from the EasyStart Web site or app is picked up at the next poll,
  not instantly.
- No live updates: it is cloud polling, so there is a delay of up to a few minutes
  unless Home Assistant sent the command itself.
