# Security-focused functional test plan

Scope: this repository (`custom_components/eberspaecher`, v0.5.1, commit `032203c`).
Method: source review plus hypothesis checks run only in the local pytest harness
(`pytest-homeassistant-custom-component`, mocked cloud through `aioclient_mock`). No
request was sent to myeberspaecher.com and no heater was started.

Evidence labels used below:

- **Confirmed behaviour**: read in the code and/or shown by a local harness run.
- **Confirmed weakness (harness)**: a local test reproduced the unsafe behaviour.
- **Potential weakness**: suggested by the code, not yet demonstrated.
- **Hypothesis**: a test idea whose outcome is unknown.
- **N/A**: the expected feature does not exist in this repository.

---

## 1. Architecture and trust boundaries

### 1.1 What this repository is (and is not)

This is a **Home Assistant (HA) custom integration** that runs inside the HA Core Python
process. It has **no UI, HTTP server, routes, REST API, OpenAPI schema, sessions, cookies,
user accounts or roles of its own**. Everything user-facing is rendered and protected by HA:

| Surface | Provided by | Protected by |
|---|---|---|
| Entities (switches, number, sensors, button, date) | `switch.py`, `number.py`, `sensor.py`, `button.py`, `date.py` | HA auth + HA entity permission policy (`POLICY_CONTROL`/`POLICY_READ`) |
| Service `eberspaecher.start_heating` | `switch.py` `async_setup_entry` → `async_register_entity_service`; schema in `services.yaml` | HA auth + entity policy (checked by HA's entity service helper) |
| Setup / reauth / options flows | `config_flow.py` (`EberspaecherConfigFlow`, `EberspaecherOptionsFlow`) | HA: config-entry flows are admin-only |
| Diagnostics download | `diagnostics.py` `async_get_config_entry_diagnostics` | **Any authenticated user** (HA 2026.2: the HTTP download view has no admin check; only the websocket list does). Confirmed by `test_sec_authz_02_diagnostics_any_user_can_download_hold_no_secrets`. |
| Repairs issues | `coordinator.py` (`cloud_unreachable`), `reminder.py` | HA: admin UI |

**Confirmed N/A in this repo:** login UI, password reset, user/role model, multi-home
tenancy, device registration or pairing, ownership transfer, CSRF/cookie handling, refresh
tokens, OpenAPI. Tests for those belong to HA Core or to Eberspächer's cloud, not here.
Where the integration *relies* on HA for them, this plan tests that reliance.

### 1.2 Components

1. **Cloud API client**: `api.py` `EberspaecherApi`. It calls the private, undocumented
   EasyStart Web API at a hard-coded `https://www.myeberspaecher.com/escw-application-server/rest/v1`:

   | Method | Path | Function | Purpose |
   |---|---|---|---|
   | POST | `/authenticate` | `async_login` | Headers `ESCW-Auth-Email`, `ESCW-Auth-Password` → `{"token"}` |
   | GET | `/calls?fetchHeater=NO&email=CURRENT&page=0&size=50` | `async_get_calls` | Devices on the account |
   | GET | `/calls/{imei}/heaters` | `async_get_heaters` | Heater state |
   | PUT | `/calls/{imei}/heaters/{address}` | `async_set_heater` | **Start/stop: physical actuation** |
   | POST | `/calls/{imei}/temperature` | `async_request_temperature` | Wakes the device to measure |

   Token: `ESCW-Auth-Token` header, kept in memory only. On 401/403 the client drops the
   token, logs in again and retries once (`_async_request`).
2. **Coordinator**: `coordinator.py` `EberspaecherCoordinator`. Polls the cloud, caches the
   device list for 6 h, holds `runtimes`, `paused_until`, and creates the `learner`,
   `scheduler`, `work_scheduler` and `reminder`.
3. **Autonomous actuation**: `schedule.py` `ScheduleManager` (legs `home` and `work`).
   It wakes at computed times and **starts the heater with no user in the loop**, based on
   other HA entities (temperature sensor or weather, phone tracker) and the car's reading.
4. **Learning**: `learning.py` `DepartureLearner`. Turns a phone sensor (e.g. Audio
   Output = `CarPlay`) and a tracker into per-weekday departure times. It reads up to 56 days
   of recorder history, persists samples and `last_home` to `.storage/eberspaecher.<entry>.departures`,
   and moves the schedule's ready-by by up to ±120 min (`MAX_SHIFT`).
5. **Idle reminder**: `reminder.py` `ReminderManager` (Store `eberspaecher.<entry>.reminder`).
6. **Notifications**: `util.py` `async_notify`. Calls a configurable service string.
7. **Persistence**: the config entry stores email and password in plaintext in HA's
   `.storage/core.config_entries` (HA norm). It also keeps the Store files above and
   restore-state data (run time, pause date).

### 1.3 Roles (from HA, not this repo)

| Role | Can do (confirmed in harness unless noted) |
|---|---|
| HA owner/admin | Everything, including options flow, diagnostics and credentials (via flows) |
| HA non-admin user (default policy) | **Start/stop the heater, call `start_heating`, change the run time, pause and schedule switch**, read all entity states and attributes (learned departure times, pause date). Confirmed: a default non-admin user started the heater. |
| HA user with a restricted entity policy (e.g. `system-read-only` group) | Blocked from `start_heating`, `switch.turn_on` and `date.set_value` with `Unauthorized`. Confirmed in harness. |
| System (no user context) | The schedule and learner. They act via `hass.services.async_call` without a user context, so policies do not apply (by design). |
| Anything that can write the input entities | The phone app (mobile_app webhook), MQTT, template sensors and so on. They indirectly decide when fuel is burned. |

### 1.4 Trust boundaries

```
 TB1  HA user (admin / non-admin / restricted) ──► HA Core ──► integration
       service calls, entity changes, options flow (admin), diagnostics (admin)

 TB2  integration ──HTTPS──► myeberspaecher.com ──GSM──► heater in the car
       credentials, token, IMEI in URL paths, cloud-supplied JSON (names, imei, address, caps)

 TB3  other HA entities ──► schedule / learner decisions (no user in the loop)
       temperature sensor/weather, device_tracker/person, Audio Output sensor, recorder history
       (often fed by the phone through the mobile_app webhook)

 TB4  integration ──► notify service ──► third-party push (APNs/FCM, chat, e-mail)
       message text, including error strings

 TB5  HA storage and backups: credentials, departure pattern, last time at home

 TB6  supply chain: GitHub/HACS release ──► code running in HA Core with full privileges
       CI: actions pinned to @v4/@v5/@main/@master; test deps unpinned
```

### 1.5 Security-sensitive workflows

- **Device control**: `EberspaecherRunSwitch.async_turn_on/async_turn_off`,
  `async_start_heating` (`switch.py`), `ScheduleManager.async_run` → `_start_all`
  (`schedule.py`), `EberspaecherRequestTemperature.async_press` (`button.py`),
  `ScheduleManager._car_reading`.
- **Credential handling**: `config_flow.py` `async_step_user`, `async_step_reauth_confirm`,
  `_async_validate`; `api.py` `async_login`.
- **Data that drives actuation**: `ScheduleManager._temperature`, `_entity_temperature`,
  `_skip_reason`, `config_for`; `util.parse_temperature`; `DepartureLearner.record`,
  `samples_from_history`, `classify`.
- **Data exposure**: `diagnostics.py`, error strings in `HomeAssistantError`
  (`command_failed`, `temperature_failed`), `last_result` attributes on
  `switch.<device>_schedule`, repairs placeholders, notifications.

### 1.6 Physical-safety note

The device is a **fuel-burning parking heater** (Hydronic). An unwanted start wastes fuel
and drains the car battery. In an enclosed garage it can produce exhaust gases. All tests
that would start a heater must run against the mocked cloud (`aioclient_mock`) only. **Never
run them against the real cloud or a real vehicle.** The repository's device name in
the maintainer's install ("Garage Optima") suggests a garage, which is a reason to treat
automatic starts as safety-relevant.

---

## 2. Findings from the analysis

| # | Status | Finding | Evidence |
|---|---|---|---|
| F1 | **Confirmed weakness (harness)** | A non-finite temperature (`"nan"`, `"-inf"`) from the temperature sensor makes the schedule start the **longest heating run (100 min)**. `parse_temperature` accepts `float("nan")`, and every band comparison in `choose_action` is False for NaN, so it falls through to `very_cold_runtime`. | Local run: sensor state `nan` → `PUT /calls/{imei}/heaters/1 {"runtime":100,"operationMode":"HEATING"}`. Same for `-inf`. |
| F2 | **Confirmed weakness (harness)** | On an account with several devices, one schedule wake-up **starts every heater on every device** (`_start_all` loops all devices). For an account with two cars, both start. `_heater_reading` also uses the first heater of any device as "the car's" temperature. | Local run with two IMEIs: two PUTs. README says it was built for one device. |
| F3 | **Confirmed weakness (harness)** | Failure text includes the **IMEI and the full API path**, for example `Could not send the command: PUT /calls/<IMEI>/heaters/1 failed: 500 …`. It appears in the push notification body, in `last_result` on the Schedule switch (readable by every HA user and kept in recorder history), and in the service error toast. The `cloud_unreachable` repair shows the same for GET. | Local run: notification message contained the IMEI. |
| F4 | **Confirmed weakness (harness)** | The notification setting accepts **any service** (`util.async_notify` partitions `domain.name` and calls it). Setting `script.x` or another service runs it with `{title, message}` on every schedule event. It can only be set by an admin (options flow), so it is defence in depth, not privilege escalation. | Local run: `script.anything` was called. |
| F5 | **Confirmed weakness (harness)** | A persistent 403 makes every request log in again (credentials resent) before failing. 5 button presses gave 9 logins. With no throttle on manual commands, any HA user can drive repeated logins and device wake-ups, which risks cloud account lockout and device or data costs. | Local run. |
| F6 | **Confirmed behaviour (platform)** | Any default non-admin HA user can start or stop the heater and read the learned leave-home time, which says when the home is usually empty, plus the pause date (away for days). A restricted-policy user is correctly blocked. | Local run with `Context(user_id=…)`. |
| F7 | Confirmed behaviour | Options-flow schemas reject unexpected keys (no mass assignment); for example `notify_service` cannot be injected via the `work` step. | Local run: `vol.Invalid`. |
| F8 | Confirmed behaviour | No template rendering, `eval`, subprocess or `verify_ssl=False` anywhere. TLS goes through HA's shared aiohttp session. | `grep` over `custom_components/`. |
| F9 | Potential weakness | Cloud-supplied `imei` and `address` are interpolated into URL paths unencoded (`f"/calls/{imei}/heaters"`). A malicious or compromised cloud reply containing `../` or `?` could redirect requests (with the token) to other paths on the same host. Requires a hostile cloud. | `api.py`, `coordinator._index_heaters` (address is `int()`-coerced, imei is not). |
| F10 | Potential weakness | Debug logging dumps the raw heater JSON (`coordinator.py` "Raw heaters …"). Unknown cloud fields may hold identifiers and end up in shared debug logs. | Code. Field set unknown. |
| F11 | Potential weakness | Anyone who can write the tracker or Audio Output state, such as the mobile_app webhook (the webhook ID is a bearer secret) or MQTT, can trigger or suppress starts and poison learning. Learning shifts are bounded at ±120 min, and the work leg needs 3 h away. | `learning.py`, `schedule._skip_reason`. |
| F12 | Potential weakness | Supply chain: `hacs/action@main` and `home-assistant/actions/hassfest@master` are mutable refs, and `requirements_test.txt` is unpinned. | `.github/workflows/*.yaml`. |
| F13 | Confirmed behaviour | `_last_start_date` (the once-a-day guard) is in memory only. After a restart the guard relies on the cloud reporting the heater as ON. | `schedule.py`. |

---

## 3. Test cases

Conventions for automation: pytest + `pytest-homeassistant-custom-component`,
`asyncio_mode = auto`, fixtures `hass`, `entry`, `cloud`, `aioclient_mock`, `freezer`,
`hass_storage`, helpers `setup`, `open_options`, `set_heater_reply`, `calls_to`, `heater`
(`tests/conftest.py`, `tests/test_integration.py`). All device control goes to the mocked cloud.

### A. Authentication against the cloud (TB2)

**SEC-AUTH-01: Credentials only sent to the fixed HTTPS host, in headers**
- Component: API client · Security area: Credential handling · Severity: High
- Risk/threat: Credential disclosure via URL, query string, plain HTTP or wrong host.
- Files: `api.py` (`BASE_URL`, `async_login`); existing `tests/test_api.py::test_login_sends_credentials_over_https_headers`
- Endpoint/function: `POST /authenticate`, `EberspaecherApi.async_login`
- Preconditions: mocked cloud.
- Steps: 1) Log in. 2) Inspect every recorded request: scheme, host, query, body, headers.
- Expected: Only `https://www.myeberspaecher.com/...`. Password only in the `ESCW-Auth-Password` header of `/authenticate`. Never in a query, a body, any other request or the token header.
- Failure: Password in a URL or query, in a non-auth request, or sent over `http://`.
- Impact: Account takeover, which means remote control of the car heater.
- Automation: Extend the existing test. Assert across `aioclient_mock.mock_calls` that `PASSWORD` appears only in that header.
- OWASP: ASVS V2.1/V9.1 · Top10 A02, A07 · API2

**SEC-AUTH-02: Secrets never appear in errors, logs, diagnostics, notifications or repairs**
- Component: API client, coordinator, schedule, diagnostics · Area: Information exposure · Severity: High
- Risk: Password or token leaks through error strings that are shown to users, logged or pushed.
- Files: `api.py`, `coordinator.py` (repairs placeholder), `schedule.py` (`failed_body`), `diagnostics.py`; existing `test_connection_error_messages_do_not_leak_secrets`, `test_login_http_error_message_has_no_password`
- Function: `_async_request`, `_async_update_data`, `async_run`, `async_get_config_entry_diagnostics`
- Preconditions: debug logging on; mocked cloud returning 500, timeout, `ClientError` and non-JSON for each endpoint.
- Steps: For each failure mode, trigger login, poll, command, temperature request and a scheduled start. Collect `caplog.text`, raised messages, the repairs issue placeholders, notification payloads, `last_result` and diagnostics.
- Expected: Neither `PASSWORD` nor the token appears in any of them.
- Failure: Any occurrence.
- Impact: Credential disclosure to every HA user, to logs shared in bug reports, and to third-party push providers.
- Automation: A parametrized pytest over failure modes and sinks.
- OWASP: ASVS V7.1, V8.3 · A09, A02 · API3

**SEC-AUTH-03: Invalid credentials are not retried in a loop**
- Component: API client · Area: Brute-force/lockout · Severity: Medium
- Risk: Repeated logins with a bad password lock the cloud account.
- Files: `api.py`, `coordinator.py` (`ConfigEntryAuthFailed`); existing `test_persistent_401_raises_auth_without_looping`, `test_auth_failure_at_setup_starts_reauth`
- Steps: 1) Cloud returns 401 for `/authenticate`. 2) Run setup and several polls over time (freezer).
- Expected: One login attempt per action, after which reauth is started and polling of the cloud stops (the entry is not loaded). There is no background retry with the same bad password.
- Failure: Repeated `/authenticate` calls with the stored password after the first 401.
- Impact: Cloud account lockout (availability), suspicious-login alerts.
- Automation: Count `calls_to(mock, "POST", "/authenticate")` after advancing time.
- OWASP: ASVS V2.2 · A07 · API4

**SEC-AUTH-04: A persistent 403 does not amplify logins (F5)**
- Component: API client · Area: Rate limiting/resource abuse · Severity: Medium
- Risk: Each call after a 403 re-sends credentials. Manual presses multiply this.
- Files: `api.py` `_async_request`; `button.py`
- Steps: 1) Cloud gives 200 for login and 403 for `POST /calls/{imei}/temperature`. 2) Press the button 5 times.
- Expected (target): Logins are bounded, for example by not re-logging in on 403 when a token was just issued, or by backing off.
- Current result: **9 logins for 5 presses (confirmed, harness).**
- Failure: Logins grow linearly with presses.
- Impact: Credential re-transmission, cloud lockout.
- Automation: pytest counting `/authenticate` calls. It will fail until a fix lands, so mark it `xfail(strict=True)` and remove the mark when fixed.
- OWASP: ASVS V2.2, V11.1 · A04 · API4

**SEC-AUTH-05: Malformed or hostile login responses**
- Component: API client · Area: Auth bypass/robustness · Severity: Medium
- Files: `api.py` `async_login`; existing tests for 200 without a token and non-JSON
- Steps: Login replies with `{"token": ""}`, `{"token": null}`, `{"token": 123}`, `[]`, a 10 MB body, a JSON bomb, and a redirect (302) to another host.
- Expected: Auth or connection error. No request is sent with an empty or non-string token. Redirects to other hosts are not followed with credentials.
- Failure: The integration proceeds unauthenticated, crashes HA's event loop, or follows the redirect with credential headers.
- Impact: Credential leak via redirect, or a DoS of HA.
- Automation: pytest with `aioclient_mock` responses. **Hypothesis to check:** aiohttp follows redirects by default and keeps custom headers on cross-host redirects. Verify by asserting `allow_redirects` behaviour.
- OWASP: ASVS V9.2, V13.1 · A07, A10 · API10

**SEC-AUTH-06: Reauth can only change the password of the same account**
- Component: Config flow · Area: Credential handling · Severity: Medium
- Files: `config_flow.py` `async_step_reauth_confirm`; existing `test_reauth_updates_password`
- Steps: 1) Start reauth. 2) Submit a wrong password, then a right one. 3) Try to inject an `email` key.
- Expected: The email is fixed from the entry, and only the password is updated after it has been validated against the cloud. An extra `email` key is rejected by the schema.
- Failure: The account can be switched through reauth, or the password is stored without validation.
- Automation: pytest via `hass.config_entries.flow`.
- OWASP: ASVS V2.5 · A07

**SEC-AUTH-07: Duplicate account and case handling**
- Area: Account confusion · Severity: Low
- Files: `config_flow.py` (`async_set_unique_id(email.lower())`); existing `test_duplicate_account_case_insensitive`
- Steps: Add `User@Example.com` after `user@example.com`, and with surrounding whitespace.
- Expected: Aborted as already configured.
- Automation: Existing test. Add a whitespace variant.
- OWASP: ASVS V2.1

### B. Authorization: reliance on HA (TB1)

**SEC-AUTHZ-01: Restricted users cannot control the heater**
- Component: Switch, service, number, date, button · Area: Function-level authorization · Severity: Critical
- Risk: A user who should only see the car can start a combustion heater.
- Files: `switch.py` (`async_register_entity_service("start_heating")`, `EberspaecherRunSwitch`, `EberspaecherScheduleSwitch`), `number.py`, `date.py`, `button.py`
- Functions: `eberspaecher.start_heating`, `switch.turn_on/turn_off`, `number.set_value`, `date.set_value`, `button.press`
- Preconditions: an HA user in the `system-read-only` group, or with a policy granting read on only one entity.
- Steps: Call each service with `context=Context(user_id=<restricted>)`.
- Expected: `homeassistant.exceptions.Unauthorized` for every control call, and no PUT/POST to the mocked cloud.
- Current result: **Confirmed** for `start_heating`, `switch.turn_on` and `date.set_value`. `number.set_value` and `button.press` still need testing.
- Failure: Any cloud command recorded, or no `Unauthorized`.
- Impact: Unauthorized physical actuation.
- Automation: pytest creating users via `hass.auth.async_create_user(..., group_ids=["system-read-only"])`. This becomes a regression test against any future change, such as a custom service registered with `hass.services.async_register` that skips the entity policy check.
- OWASP: ASVS V4.1, V4.2 · A01 · API5

**SEC-AUTHZ-02: Admin-only surfaces stay admin-only**
- Component: Options flow, diagnostics · Area: Vertical privilege escalation · Severity: High
- Risk: A non-admin changes the schedule, notification target or learning sources, or downloads diagnostics.
- Files: `config_flow.py` `EberspaecherOptionsFlow`, `diagnostics.py`
- Endpoints (HA): `POST /api/config/config_entries/options/flow`, `GET /api/diagnostics/config_entry/<id>`
- Steps: With `hass_client` authenticated as a non-admin user, call both endpoints.
- Expected: 401/403.
- Failure: Flow started or diagnostics returned.
- Impact: The notify target could be pointed at another service (see F4), the schedule bands manipulated, or data exfiltrated.
- Automation: pytest with `hass_client` / `hass_client_no_auth` and a non-admin token. This tests HA platform behaviour the integration depends on, so keep it lightweight.
- OWASP: ASVS V4.1 · A01 · API5

**SEC-AUTHZ-03: Cross-account isolation between config entries**
- Component: Coordinator, schedule · Area: Horizontal escalation/BOLA · Severity: High
- Risk: Entry A's schedule or entities command heaters of entry B (a different cloud account).
- Files: `schedule.py` `_start_all` (looks up `switch` by unique_id `f"{imei}_{address}_heating"`), `entity.py` (unique IDs)
- Preconditions: two config entries (accounts A and B) with different IMEIs, each with its own `aioclient_mock` token.
- Steps: 1) Run entry A's scheduled start. 2) Call `start_heating` on A's switch. 3) Repeat with B.
- Expected: Only A's IMEI receives PUTs, each with A's token.
- Failure: A PUT to B's IMEI, or one carrying A's token.
- Hypothesis: If the **same IMEI** is visible in both accounts, the unique IDs collide. The second entry's entities are not created, and `_start_all` may resolve to the other entry's switch and send the command with the other account's token. Verify.
- Automation: pytest with two `MockConfigEntry`s.
- OWASP: ASVS V4.2 · A01 · API1

**SEC-AUTHZ-04: Privacy of learned departure data (F6)**
- Component: Learned sensors, Schedule switch attributes, pause date · Area: Excessive data exposure · Severity: Medium
- Risk: The learned leave-home time reveals when the home is usually empty, and the pause date reveals days away.
- Files: `sensor.py` `EberspaecherLearnedSensor`, `switch.py` `extra_state_attributes`, `date.py`
- Steps: 1) Check that the learned sensors have `entity_category == DIAGNOSTIC`. 2) Check they are not exposed to voice assistants by default (`homeassistant.components.homeassistant.exposed_entities`). 3) As a restricted user, read the states via the websocket `get_states` and confirm HA filters them.
- Expected: Diagnostic category, not exposed by default, and filtered for users without read permission.
- Failure: Exposed by default, or readable despite the policy.
- Automation: pytest. For assistant exposure use `async_should_expose(hass, "conversation", entity_id)`.
- OWASP: ASVS V8.3 · A01 · API3

### C. Service and API input validation (TB1)

**SEC-API-01: `start_heating` input boundaries and types**
- Component: Switch service · Area: Input validation · Severity: High
- Risk: An out-of-range run time or unsupported mode reaches the heater.
- Files: `switch.py` (schema `vol.Range(min=1, max=600)`, `vol.In([HEATING, VENTILATION])`, `async_start_heating` limit check), `services.yaml`; existing `test_service_rejects_bad_input`, `test_service_runtime_outside_heater_limits_rejected`, `test_service_rejects_unsupported_mode`
- Steps: Send runtime `0, -1, 9, 10, 120, 121, 600, 601, 1e9, "30", "abc", null, true, 30.7`, and mode `OFF, off, heating, "", null, ["HEATING"]`.
- Expected: Only values inside the heater's own capability range and supported modes produce a PUT. `true` must not be coerced to 1, and `OFF` must not be accepted (stopping uses `turn_off`).
- Failure: Any PUT with an out-of-range runtime or unexpected mode.
- Hypothesis: `vol.Coerce(int)` accepts `True` as 1 (rejected later by the heater minimum of 10) and `30.7` as 30. Confirm and decide.
- Automation: A parametrized pytest asserting on `calls_to(cloud, "PUT", ...)`.
- OWASP: ASVS V5.1 · A03, A04 · API8

**SEC-API-02: The run-time number respects heater limits**
- Area: Input validation · Severity: Medium
- Files: `number.py` `EberspaecherRunTime`; existing `test_number_rejects_out_of_range`
- Steps: Set values outside the limits, and a restored state outside the limits after the heater's caps change.
- Expected: Rejected, and a restored out-of-range value is ignored.
- Automation: Existing test. Add the restore case.
- OWASP: ASVS V5.1

**SEC-API-03: Options-flow mass assignment and type confusion (F7)**
- Component: Options flow · Area: Mass assignment · Severity: Medium
- Files: `config_flow.py` all `async_step_*`, `_save`
- Steps: Submit each step with extra keys (`schedule_enabled`, `notify_service`, `learn_tracker`), wrong types (a list for `ready_by`, a string for `vent_runtime`) and extremes (`-50`/`60` °C, run time `600`).
- Expected: Extra keys are rejected (`vol.Invalid`), and contradictory values give step errors (`bands_order`, `runtime_too_long`, `vent_below_heating`, `work_needs_tracker`, `learn_state_binary`).
- Current result: Extra keys rejected (confirmed, `work` step).
- Automation: pytest per step.
- OWASP: ASVS V5.1 · API6

**SEC-API-04: Stored options that are corrupt or tampered with**
- Area: Input validation (persistence) · Severity: Medium
- Files: `schedule.py` `ScheduleConfig.from_options`, `learning.py` `LearnConfig.from_options`, `coordinator.idle_interval`; existing `test_bad_stored_option_falls_back`
- Steps: Load an entry whose options hold `ready_by: "25:99"`, `weekdays: "mon"` (a string), `very_cold_runtime: "abc"`, `learn_margin: -100`, `notify_service: 123`.
- Expected: Safe defaults, no crash and no start outside the defaults.
- Failure: An exception during setup, or an unexpected start time.
- Hypothesis: `weekdays: "mon"` is iterated as characters and gives an empty tuple, so the schedule is silently off. Confirm it is safe.
- Automation: pytest with `MockConfigEntry(options=...)`.
- OWASP: ASVS V5.1

**SEC-API-05: The notification target is restricted to notify services (F4)**
- Component: `util.async_notify` · Area: Function-level authorization (confused deputy) · Severity: Medium
- Steps: Set `notify_service` to `script.x`, `lock.unlock`, `homeassistant.restart`, `shell_command.x`, `notify.mobile_app_phone` and `mobile_app_phone`. Trigger a schedule event.
- Expected (target): Only the `notify` domain is called. Anything else is refused with a warning.
- Current result: **Any service is called (confirmed, harness).** Only an admin can set it.
- Failure: A non-notify service runs.
- Impact: The integration becomes a periodic trigger for arbitrary services, including physical ones.
- Automation: pytest with `async_mock_service` for several domains. Mark it `xfail(strict=True)` until fixed.
- OWASP: ASVS V4.2, V1.4 · A04 · API5

### D. Data that drives physical actuation (TB3)

**SEC-DEV-01: Non-finite or absurd temperatures never start the heater (F1)**
- Component: Schedule · Area: Dangerous state transition · Severity: **Critical**
- Risk: A broken or spoofed sensor makes the schedule start the longest heating run.
- Files: `util.py` `parse_temperature`, `schedule.py` `choose_action`, `_entity_temperature`, `_heater_reading`
- Preconditions: the `scheduled` fixture (`tests/test_schedule.py`), mocked cloud.
- Steps: Set the temperature sensor (and, separately, a weather entity's `temperature` attribute and the cloud's `lastMeasuredTemperature`) to `nan`, `NaN`, `inf`, `-inf`, `1e308`, `-1e308`, `-273.16`, `" -5 "`, `"5,0"`, `"--5"`, and `{"temperature":"nan"}`. Run `async_run` at every start time.
- Expected: Non-finite and physically impossible values (for example below -60 °C or above 70 °C) count as "no reading": no PUT, and the "not started" result at the last wake-up.
- Current result: **`nan` and `-inf` cause a 100-minute heating start (confirmed, harness).**
- Failure: Any PUT for these inputs.
- Impact: Unwanted combustion heater runs, which waste fuel, drain the battery and, in a garage, create an exhaust hazard.
- Automation: A parametrized pytest. Release-blocking.
- OWASP: ASVS V5.1.4 · A04 · API8 (logic)

**SEC-DEV-02: A schedule only commands the intended heater (F2)**
- Component: Schedule · Area: Device identifier/ownership scoping · Severity: **High**
- Risk: An account with two cars starts both. The temperature from car 1 decides for car 2.
- Files: `schedule.py` `_start_all`, `_heater_reading`, `_raw_reading`
- Preconditions: mocked `/calls` returning two IMEIs.
- Steps: Run a scheduled start, and run the work leg's car reading.
- Expected (target): The schedule targets a chosen heater, or the setup refuses or warns for multi-device accounts. The temperature comes from the same heater it starts.
- Current result: **Both devices started (confirmed, harness).**
- Failure: A PUT to a device the user did not choose.
- Impact: Unintended physical actuation of another vehicle, possibly belonging to a family member.
- Automation: pytest with two IMEIs. Release-blocking for multi-device support.
- OWASP: ASVS V4.2 · A01 · API1

**SEC-DEV-03: Location gating cannot be bypassed**
- Component: Schedule (`_skip_reason`), learner · Area: Business-logic authorization · Severity: High
- Files: `schedule.py` `_skip_reason`, `_work_view` (`learn_tracker` required), `config_flow.async_step_work`
- Steps: For the work leg, set the tracker to `home`, `unavailable`, `unknown`, `""`, `HOME`, `Home ` (trailing space), `zone.home`, a deleted entity, or a `person` whose source is unknown. For the home leg, set `not_home`.
- Expected: The work leg never starts while home or unknown. The home leg skips when not home. Comparisons are case-insensitive, and odd values are treated as unknown.
- Failure: A start under any of these states.
- Hypothesis: `"Home "` (trailing space) is not `"home"` after `casefold()`, so the work leg would treat it as away. Real trackers don't emit this, but templates could.
- Automation: A parametrized pytest using the `at_work` fixture.
- OWASP: ASVS V4.2, V11.1 · A04

**SEC-DEV-04: Learning poisoning stays bounded**
- Component: Learner · Area: Data integrity · Severity: Medium
- Risk: Someone who can write the phone's Audio Output or tracker state (mobile_app webhook or MQTT) shifts start times.
- Files: `learning.py` (`MAX_SHIFT`, `MIN_SAMPLES`, `AWAY_BEFORE_WORK`, `classify`, `record`), `schedule.config_for`
- Steps: 1) Inject 50 `CarPlay` toggles a day at extreme times. 2) Inject samples at 04:00 and 23:59. 3) Flip the tracker quickly around connections. 4) Write a corrupt Store file (non-int minutes, future dates, huge dicts).
- Expected: One sample per day per leg. The ready-by never moves more than 120 min from the fixed time, and only if the longest run still fits. A corrupt Store gives an empty or valid state with no crash.
- Failure: A shift above 120 min, a start before midnight, or a crash on load.
- Hypothesis: `async_load` does `int(minutes)` without a try, so a corrupt Store raises at setup and blocks the integration. Verify.
- Automation: pytest with `hass_storage` pre-seeded and `freezer`.
- OWASP: ASVS V5.1, V11.1 · A04, A08

**SEC-DEV-05: The work leg never uses a stale car reading**
- Component: Schedule `_car_reading` · Area: Dangerous state transition · Severity: High
- Files: `schedule.py` (`CAR_READING_TRIES`, `CAR_READING_REUSE`, timestamp comparison)
- Steps: The request fails; the request succeeds but the timestamp is unchanged; the timestamp changes with a NaN value; the reading has no timestamp; the reload happens during the wait.
- Expected: Only a renewed, finite reading is used, and nothing starts after a stop or reload.
- Current result: Covered by the v0.5.1 tests, except the NaN-with-new-timestamp case (see SEC-DEV-01).
- Automation: Existing tests in `tests/test_learning.py`, plus the NaN case.
- OWASP: ASVS V11.1 · A04

**SEC-DEV-06: Pause and schedule switch cannot be overridden by automation timing**
- Component: Schedule, date · Area: State-transition integrity · Severity: Medium
- Files: `schedule.py` `paused_on`, `async_run`, `_async_announce`; `date.py`
- Steps: Pause until today, then run every wake-up. Clear the pause in the middle of the day. Set the pause to a far-future date. Restart HA with a pause set (restore). Change the time zone so a date boundary moves.
- Expected: No starts on paused days, the pause survives restarts, and there are no announcements for paused days.
- Automation: Extend the existing pause tests in `tests/test_schedule.py`.
- OWASP: ASVS V11.1

**SEC-DEV-07: Once-a-day guard and races**
- Component: Schedule, switch · Area: Replay/race · Severity: Medium
- Files: `schedule.py` (`_last_start_date`, `_start_all` skipping `STATE_ON`), `switch.py` (pending state)
- Steps: 1) Fire the same wake-up twice concurrently with `asyncio.gather`. 2) Start, restart HA (reload the entry) while the cloud still says OFF (lagging), then fire the next wake-up of the same band. 3) Manual `turn_on` concurrently with a scheduled start.
- Expected: At most one PUT per leg per day. A manual and a scheduled start don't double-send within the pending window.
- Hypothesis: Concurrent wake-ups both pass the `_last_start_date` check before either sets it, because the check happens before awaiting the temperature. Verify with `gather`.
- Automation: pytest with `asyncio.gather` and counts of `calls_to`.
- OWASP: ASVS V11.1.6 · A04

**SEC-DEV-08: Offline and degraded cloud behaviour fails safe**
- Component: Coordinator, switch, schedule · Area: Offline security behaviour · Severity: Medium
- Files: `coordinator.py` (`UpdateFailed`, repairs), `switch.py` (`_pending_expired`), `schedule.py`
- Steps: The cloud times out during a scheduled start. The cloud returns 200 but the heater never turns on. A device disappears from `/calls`. A stop command fails while the heater runs.
- Expected: The user is told on failure (notification and `last_result`). Entities become unavailable when the device is gone. A failed stop raises an error and the switch keeps showing ON (not a false OFF).
- Failure: A silent failure, or the UI showing OFF while the heater is running.
- Automation: pytest. Some cases already exist (`test_command_failure_surfaces_and_keeps_state`, `test_device_vanishes_entities_unavailable`). Add the failed-stop case.
- OWASP: ASVS V7.4, V11.1 · A04

**SEC-DEV-09: Cloud-supplied identifiers cannot redirect requests (F9)**
- Component: API client, coordinator · Area: Injection/path manipulation · Severity: Low (needs a hostile cloud)
- Files: `api.py` (`f"/calls/{imei}/..."`), `coordinator._index_heaters`, `entity.py` (unique_id from imei)
- Steps: `/calls` returns imei values `"../../x"`, `"a?b=c"`, `"a#b"`, `"a/b"`, `""`, `123`, and a 10 kB string. Heater `address` values: `"1/../2"`, `-1`, `1e9`.
- Expected: Unusable identifiers are skipped (or URL-encoded), and no request is sent to a path outside `/calls/{imei}/...`.
- Failure: A request to another path on the host carrying the token.
- Automation: pytest asserting every recorded request path matches `^/escw-application-server/rest/v1/calls/[^/]+/(heaters(/\d+)?|temperature)$`.
- OWASP: ASVS V5.3, V12.6 · A03, A10 · API8, API10

**SEC-DEV-10: Hostile or oversized cloud payloads don't crash HA or inject markup**
- Component: Coordinator, entities · Area: Input validation (stored content) · Severity: Low
- Files: `coordinator.py`, `entity.py` (device name), `switch.py` attributes (`errors`), `sensor.py`
- Steps: Device `name` `<img src=x onerror=alert(1)>`, heater errors as a huge list, nested dicts 1000 levels deep, capabilities with negative or huge limits.
- Expected: Stored as plain text (rendering is HA frontend's job), limits fall back to defaults, no exception, and no hang.
- Automation: pytest. Frontend escaping is out of scope and can be checked manually in a test HA.
- OWASP: ASVS V5.3 · A03

### E. Session and token (TB2)

**SEC-SESS-01: Token lifecycle**
- Component: API client · Area: Token reuse/invalidation · Severity: Medium
- Files: `api.py` (`_token`, `_login_lock`, `_async_request`); existing `test_token_reused_between_requests`, `test_expired_token_relogin_once_then_succeeds`, `test_concurrent_requests_login_only_once`
- Steps: 1) The token expires (401) during concurrent requests. 2) Unload or remove the entry, then check the token is dropped. 3) Reauth with a new password, and check the old token isn't used.
- Expected: A single re-login under concurrency. The token is never persisted to disk, and a new API object after reauth starts without a token.
- Automation: pytest. Assert `hass_storage` never contains the token string.
- OWASP: ASVS V3.3, V3.5 · A07 · API2

**SEC-SESS-02: Logout, session fixation, cookies, CSRF, refresh tokens**
- Status: **N/A in this repository.** HA Core handles user sessions. The cloud token has no refresh token or cookie in this client.
- Action: No integration test. Mention it in the threat model only.

### F. Configuration and information exposure (TB4, TB5, TB6)

**SEC-INFO-01: Diagnostics redaction**
- Component: Diagnostics · Area: Sensitive data exposure · Severity: High
- Files: `diagnostics.py` `TO_REDACT`; existing `test_diagnostics_hide_personal_entities`
- Steps: Fill every option (email, password, notify service, temperature entity, work temperature entity, learn entity, tracker) and heater data with `imei`, `serialNumber`, `name`, `description`, `phoneNumber`, `iccid` and `email` fields. Download the diagnostics.
- Expected: None of these values appear anywhere in the JSON (search the serialized output, not just known keys).
- Hypothesis: Unknown cloud fields such as `iccid`, `phoneNumber`, `msisdn` or `ownerEmail` are not in `TO_REDACT`. Verify against a real (anonymized) payload before deciding.
- Automation: pytest serializing diagnostics and asserting absence of each seeded value.
- OWASP: ASVS V8.3 · A01, A09 · API3

**SEC-INFO-02: Device identifier not leaked through user-visible errors (F3)**
- Component: API client, schedule, switch, button, coordinator · Area: Information leakage · Severity: Medium
- Files: `api.py` (`f"{method} {path} failed: {err}"`), `switch.py` `_async_send`, `button.py` `async_press`, `schedule.py` `async_run` (`failed_body`, `last_result`), `coordinator.py` (repairs placeholder)
- Steps: Make PUT, POST and GET fail with 500 or a timeout. Collect the notification body, `last_result`, the service error, the repairs placeholder and `caplog`.
- Expected (target): The IMEI and the full URL are absent from user-visible text and push notifications. Logs may hold the last 4 digits, as in `_index_heaters`.
- Current result: **The IMEI appears in the notification and `last_result` (confirmed, harness).**
- Impact: The device identifier spreads to every HA user, recorder history, and third-party push providers.
- Automation: pytest that asserts `IMEI not in` each sink. Mark it `xfail` until fixed.
- OWASP: ASVS V7.1, V8.3 · A09 · API3

**SEC-INFO-03: Logs at debug level**
- Area: Information exposure · Severity: Low
- Files: `coordinator.py` ("Raw heaters …"), `api.py` (`_LOGGER.debug` on connect retries)
- Steps: Enable debug, run setup with a heater payload seeded with identifying fields, and check `caplog`.
- Expected: The IMEI is shortened, and no password, token, email or serial number appears.
- Automation: pytest with `caplog.set_level(logging.DEBUG)`.
- OWASP: ASVS V7.1 · A09

**SEC-INFO-04: Personal data deleted on removal and not over-retained**
- Component: `__init__.async_remove_entry`, learner Store · Area: Privacy · Severity: Medium
- Files: `__init__.py`, `learning.py` (`KEEP_DAYS = 120`, `last_home`), `reminder.py`; existing `test_removing_the_integration_deletes_what_it_learned`
- Steps: Remove the entry and check both Store keys are gone. Check samples older than 120 days are pruned on save.
- Expected: Deleted and pruned.
- Automation: pytest with `hass_storage`. Extend it to the reminder Store and to pruning.
- OWASP: ASVS V8.3.4

**SEC-INFO-05: Credentials at rest**
- Area: Credential storage · Severity: Medium (accepted HA norm)
- Files: `config_flow.py` (`data={CONF_EMAIL, CONF_PASSWORD}`)
- Confirmed behaviour: the password is stored in plaintext in the config entry, as for other cloud integrations, and is part of HA backups unless they are encrypted.
- Test: Assert that the password appears in exactly one place in `hass_storage` (the config entry) and not in any `eberspaecher.*` Store.
- Recommendation: Document in the README that backups contain the password, and recommend encrypted backups.
- OWASP: ASVS V2.10, V6.4 · A02

**SEC-INFO-06: Supply chain and CI hardening (F12)**
- Component: `.github/workflows/*.yaml`, `requirements_test.txt`, `manifest.json` · Area: Integrity · Severity: Medium
- Steps: 1) Check actions are pinned to commit SHAs. 2) Check workflows declare least-privilege `permissions:`. 3) Check test dependencies are pinned or locked. 4) Check `manifest.json` has no runtime `requirements` (currently none, so the attack surface is HA itself).
- Expected: SHA-pinned actions, `permissions: contents: read`, pinned test dependencies.
- Current result: `hacs/action@main`, `hassfest@master`, `actions/*@v4/v5` (mutable tags); no `permissions:` block found; unpinned test dependencies.
- Automation: A CI lint step (for example `zizmor` or `actionlint`). This would be a new dev-only tool, justified because the release artifact runs with full HA privileges. Alternatively a simple grep test.
- OWASP: Top10 A08 · ASVS V14.2

---

## 4. Prioritization

### 4.1 Top 10 tests

| Rank | Test | Why it matters here |
|---|---|---|
| 1 | **SEC-DEV-01** non-finite temperature | **Confirmed**: a single bad sensor value (`nan`, `-inf`) causes an unattended 100-minute combustion-heater run. Cheap to trigger from template, MQTT or broken sensors. |
| 2 | **SEC-AUTHZ-01** restricted users can't actuate | The integration's only authorization is HA's entity policy. Any future custom service that bypasses it would allow anyone with a login to start the heater. |
| 3 | **SEC-DEV-02** one schedule commands every device | **Confirmed**: multi-car accounts start every car's heater, using one car's temperature. |
| 4 | **SEC-AUTH-02** no secrets in any sink | The password leads to full remote control. Many sinks reach people or third parties (push, history, logs, repairs). |
| 5 | **SEC-DEV-03** location gating | The work leg's safety depends on knowing you're away. A regression would heat the car while it sits in the home garage. |
| 6 | **SEC-INFO-01** diagnostics redaction | Diagnostics are made to be posted publicly in bug reports. The cloud payload schema is unknown. |
| 7 | **SEC-API-01** `start_heating` boundaries | The only free-form actuation interface, and callable from any automation or user. |
| 8 | **SEC-DEV-05** stale car readings | The car is often underground. A stale reading would decide heating without real data. |
| 9 | **SEC-API-05** notify target restriction | **Confirmed**: the "notification" setting can trigger any service, physical ones included, on every schedule event. |
| 10 | **SEC-INFO-02** IMEI in errors and push | **Confirmed**: the device ID reaches every user, recorder history and push providers. |

### 4.2 Release-blocking if they fail

- SEC-DEV-01, SEC-DEV-02 (or an explicit multi-device refusal), SEC-DEV-03, SEC-DEV-05
- SEC-AUTHZ-01, SEC-AUTHZ-03
- SEC-AUTH-01, SEC-AUTH-02, SEC-AUTH-03
- SEC-INFO-01
- SEC-API-01

That is everything that could cause unattended or unauthorized physical actuation, or credential disclosure. SEC-DEV-01 and SEC-DEV-02 **currently fail**, so with this policy the next release should include their fixes.

### 4.3 Suitable for CI/CD automation

All tests in sections A to F except the manual frontend-rendering part of SEC-DEV-10 and the
platform-level parts of SEC-AUTHZ-02 can run in the existing `tests.yaml` workflow
(Python 3.12/3.13, pytest). They need no network, devices or new dependencies. Suggested layout:

- `tests/test_security_actuation.py`: SEC-DEV-01, -02, -03, -05, -06, -07, -08
- `tests/test_security_authz.py`: SEC-AUTHZ-01, -03, -04
- `tests/test_security_exposure.py`: SEC-AUTH-02, SEC-INFO-01, -02, -03, -04, -05
- `tests/test_api.py` (extend): SEC-AUTH-01, -03, -04, -05, SEC-SESS-01, SEC-DEV-09
- `tests/test_config_flow.py` (extend): SEC-AUTH-06, -07, SEC-API-03
- An optional workflow lint for SEC-INFO-06 (new tool, dev only).

Tests for confirmed weaknesses that are not fixed yet should be added as
`@pytest.mark.xfail(strict=True, reason="SEC-…")`. CI then stays green and flags the moment a fix lands.

### 4.4 Automation patterns (from the existing suite)

```python
# Actuation is only ever observed on the mocked cloud:
puts = calls_to(cloud, "PUT", "/heaters/1")

# Acting as a specific HA user:
user = await hass.auth.async_create_user("ro", group_ids=["system-read-only"])
with pytest.raises(Unauthorized):
    await hass.services.async_call(DOMAIN, "start_heating", {"runtime": 30},
        target={"entity_id": SWITCH}, blocking=True, context=Context(user_id=user.id))

# Driving the schedule deterministically:
await entry.runtime_data.scheduler.async_run(time(6, 30), at(6, 30))
```

---

## 5. Unknowns and what to clarify

| Unknown | Why it matters | What to inspect |
|---|---|---|
| Cloud-side authorization (can a token for account A read or command IMEI B?) | That would be a BOLA in Eberspächer's service, outside this repo. **Do not test against production.** | Report it to Eberspächer if a concern. Out of scope. |
| Full heater/call JSON schema (extra identifiers such as ICCID or phone number) | Decides what diagnostics and debug logs must redact. | One anonymized real `/calls` and `/heaters` payload from the maintainer's own account. |
| Token lifetime and server-side invalidation on password change | Decides whether a leaked token stays usable. | Cloud behaviour. Observe only on your own account and only if Eberspächer's terms allow it. |
| Whether aiohttp forwards `ESCW-Auth-*` headers on cross-host redirects | Possible credential leak (SEC-AUTH-05). | Test locally with `aioclient_mock` redirect plus aiohttp version behaviour. |
| Deployment: HA users, groups, mobile_app webhook exposure, backup encryption | Decides who can actuate (F6) and who can spoof inputs (F11). | The maintainer's HA instance configuration. |
| Whether HA rejects `nan` for numeric sensors | Narrows the F1 trigger surface (template/MQTT vs native sensors). | Check with the HA version in use; the fix is still needed for weather and cloud values. |

---

## 6. Implementation results (tests run locally, mocked cloud only)

New test files, using the existing pytest + `pytest-homeassistant-custom-component` setup
with no new dependencies:

| File | Covers | Tests |
|---|---|---|
| `tests/test_security_actuation.py` | SEC-DEV-01 to SEC-DEV-10 | 53 |
| `tests/test_security_authz.py` | SEC-AUTHZ-01 to SEC-AUTHZ-04 | 17 |
| `tests/test_security_exposure.py` | SEC-AUTH-01 to -07, SEC-SESS-01, SEC-API-01, -03, -04, -05, SEC-INFO-01 to -05 | 56 |

Full suite: **325 passed**, 97% line coverage. Run against the code without the fixes,
**46 of the new tests failed**, so they detect the problems they are meant to.

### Status of findings

| Finding | Before | Now |
|---|---|---|
| F1: `nan`, `inf` or impossible temperatures start the 100 min run | Confirmed (also from weather attributes and the heater's own reading) | **Fixed.** `util.parse_temperature` rejects non-finite values, and `schedule.plausible` treats readings outside -60 to 70 °C as no reading. |
| F2: one schedule starts every device on the account | Confirmed | **Fixed.** The schedule controls and reads only `coordinator.primary_imei`, the device with the Schedule switch. |
| F3: IMEI and API URL in errors, push, `last_result`, repairs | Confirmed | **Fixed.** `api._masked` and `api._detail`: the path shows only the last 4 characters, with no URL. |
| F4: notify setting can call any service | Confirmed | **Fixed.** `util.async_notify` only calls `notify.*`, and the settings refuse anything else (`notify_not_notify`). |
| F5: each 403 re-sends the password | Confirmed (5 presses gave 9 logins) | **Fixed.** No new login when the token is less than 60 s old. One retry for older tokens is kept. |
| Corrupt learning Store blocks setup (SEC-DEV-04) | Hypothesis, **confirmed** (4 cases) | **Fixed.** `learning._valid_samples` and type checks in `async_load`. |
| Tracker `"Home "` counted as away (SEC-DEV-03) | Hypothesis, **confirmed** | **Fixed.** States are stripped before comparing. |
| Non-string tokens accepted (SEC-AUTH-05) | Hypothesis, **confirmed** (`123`, `["x"]`) | **Fixed.** |
| Redirects followed with credential headers (SEC-AUTH-05) | Hypothesis: aiohttp's default `allow_redirects=True` was in use | **Fixed.** `allow_redirects=False`, and a 3xx is an error. |
| Unusable device ids put into URL paths (F9) | Hypothesis, **confirmed** for `../`, `?`, `#`, `/` | **Fixed.** Ids must match `[A-Za-z0-9_-]{1,64}` and are URL-encoded as one segment. |
| Unknown cloud identifiers in diagnostics | Hypothesis, **confirmed** for `iccid`, `imsi`, `msisdn`, `phoneNumber`, `ownerEmail` | **Fixed** (added to `TO_REDACT`). |
| `start_heating` runtime `true` read as 1 | Hypothesis (it was rejected later by the heater minimum) | Rejected by the schema now. |
| Two wake-ups at once start twice (SEC-DEV-07) | Hypothesis, **not confirmed** (the test passed on the old code) | A per-schedule lock was added as a cheap safeguard. |
| Diagnostics admin-only (SEC-AUTHZ-02) | Assumed | **Wrong assumption:** any signed-in user can download them (a Home Assistant behaviour). The test now asserts the file holds no secrets for such a user. |
| F6: default non-admin users can control the heater | Confirmed platform behaviour | Unchanged (Home Assistant's model). Restricted users are blocked, and both are pinned by tests. |
| F12: CI actions on mutable refs, no `permissions:` | Confirmed | Not changed. Recommendation only (needs SHA lookups and the maintainer's choice). |

### Bypass review after v0.5.2 (same root causes)

| Probe | Status | Fix | Regression tests |
|---|---|---|---|
| B1: the cloud lists devices in another order, so the schedule moves to the car without the Schedule switch | **Confirmed (harness)**, High | The schedule device is pinned once in the config entry (`schedule_device`) by `coordinator.async_pin_schedule_device` and kept | `test_sec_dev_02_reordered_device_list_does_not_move_the_schedule`, `..._pinned_device_survives_a_restart_with_another_order`, `..._existing_install_is_pinned_to_its_current_device` |
| B4: the schedule's car leaves the account, so the next car takes over | **Confirmed (harness)**, High | `primary_imei` is None when the pinned device is missing, so the schedule fails closed (with a warning) | `test_sec_dev_02_schedule_stops_when_its_device_leaves_the_account` |
| B2: errors other than HTTP status (e.g. `InvalidURL`, payload errors) quoted the full URL with the IMEI | **Confirmed (harness)**, Low (device ids are now validated, so the trigger is unlikely) | `api._detail` masks device ids in any error text | `test_sec_info_02_other_client_errors_do_not_carry_the_device_id` |
| B3: diagnostics redaction matched exact key names only (`IMEI`, `SerialNumber`, `ICCID` leaked) | **Confirmed (harness)**, Low to Medium (depends on what the cloud sends; any signed-in user can download diagnostics) | Case- and underscore-insensitive redaction in `diagnostics.async_redact_data`; the new `schedule_device` is redacted too | `test_sec_info_01_identifiers_are_redacted_in_any_spelling` |
