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

1. HACS → three-dot menu → **Custom repositories** → add this repository, category
   **Integration**.
2. Install **Eberspächer EasyStart Web** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Eberspächer EasyStart Web**,
   then sign in with your myeberspaecher.com email and password.

Manual install: copy `custom_components/eberspaecher` into your Home Assistant
`custom_components` folder and restart.

Your email and password are stored in your own Home Assistant, as with other cloud
integrations. They are only sent to myeberspaecher.com.

## What you get

Per device (found automatically) and per heater:

| Entity | What it does |
|---|---|
| `switch` Heating | On starts the heater for the **Run time**; off stops it |
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
  mode: HEATING      # or VENTILATION, optional
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

## Example: be warm at 08:10 on weekdays, depending on outside temperature

```yaml
alias: Car pre-heat for 08:10
triggers:
  - trigger: time
    at: "06:30:00"
    id: very_cold
  - trigger: time
    at: "07:00:00"
    id: cold
  - trigger: time
    at: "07:30:00"
    id: mild
conditions:
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
  - condition: state
    entity_id: switch.my_heater_heating
    state: "off"
actions:
  - variables:
      t: "{{ states('sensor.outdoor_temperature') | float(99) }}"
  - choose:
      - conditions: "{{ trigger.id == 'very_cold' and t <= -10 }}"
        sequence:
          - action: eberspaecher.start_heating
            target: { entity_id: switch.my_heater_heating }
            data: { runtime: 100 }
      - conditions: "{{ trigger.id == 'cold' and t > -10 and t <= 0 }}"
        sequence:
          - action: eberspaecher.start_heating
            target: { entity_id: switch.my_heater_heating }
            data: { runtime: 70 }
      - conditions: "{{ trigger.id == 'mild' and t > 0 and t <= 10 }}"
        sequence:
          - action: eberspaecher.start_heating
            target: { entity_id: switch.my_heater_heating }
            data: { runtime: 40 }
```

Replace the switch and temperature sensor with your own entities.

## Known limits

- Only tested against one device. The device list and heater fields are read defensively,
  but other models may expose different fields.
- Starting or stopping from the EasyStart Web site or app is picked up at the next poll,
  not instantly.
- No live updates: it is cloud polling, so there is a delay of up to a few minutes
  unless Home Assistant sent the command itself.
