# Functional test plan

What the integration should do in daily use, and how each part is tested. The security
side is in [security-test-plan.md](security-test-plan.md).

## How the tests work

- **Unit and feature tests** (`test_api.py`, `test_learning.py`, `test_schedule.py`,
  `test_integration.py`, `test_reminder.py`, `test_config_flow.py`) test each piece on its own,
  often by calling the schedule directly.
- **Whole-day tests** (`tests/test_functional.py`) call nothing by hand. They move a simulated
  clock (Helsinki time) and Home Assistant fires the wake-ups, the nightly replan and the
  evening announcement itself. The clock moves in small steps so every timer fires on time.
- The cloud is always mocked. No test talks to myeberspaecher.com or starts a real heater.

## Scenarios

| ID | Scenario | Expected | Test |
|---|---|---|---|
| FT-DAY-01 | Cold, mild and cool mornings (−12, −5, 4 °C) | One start, at 06:30 / 07:00 / 07:30, for 100 / 70 / 40 min; one notification | `test_ft_day_01_*` |
| FT-DAY-02 | Warm morning (14 °C) | No start; one "not needed" notification | `test_ft_day_02_*` |
| FT-DAY-03 | A full week | Starts Monday to Friday only | `test_ft_day_03_*` |
| FT-DAY-04 | Heater already running | Left alone | `test_ft_day_04_*` |
| FT-DAY-05 | Phone away from home in the morning | No start, "not at home" result | `test_ft_day_05_*` |
| FT-PAUSE-01 | Pause set to Thursday on Wednesday | Wednesday and Thursday skipped quietly; Friday starts | `test_ft_pause_01_*` |
| FT-PAUSE-02 | Pause cleared mid-morning | The next wake-up runs | `test_ft_pause_02_*` |
| FT-LEARN-01 | Three Wednesdays at 07:35, 07:40, 07:50 | Tuesday 20:00 announces 07:35; Wednesday starts at 05:55 | `test_ft_learn_01_*` |
| FT-LEARN-02 | The day after (nothing learned for Thursday) | Back to the fixed 08:10 | `test_ft_learn_02_*` |
| FT-LEARN-03 | Restart after learning | Learned days kept | `test_ft_learn_03_*` |
| FT-WORK-01 | A working day | Morning start at home; afternoon start from the car's own reading (asked once); leaving at 17:00 learned | `test_ft_work_01_*` |
| FT-WORK-02 | Day off at home | The work leg never starts and never wakes the car | `test_ft_work_02_*` |
| FT-OPT-01 | Ready time changed at 05:00 | Today's wake-ups move | `test_ft_opt_01_*` |
| FT-OPT-02 | Schedule switched off, then on | Off skips; on runs the next wake-up | `test_ft_opt_02_*` |
| FT-OPT-03 | New heating bands | Used at the next wake-up | `test_ft_opt_03_*` |
| FT-OPT-04 | Each form error | Error shown, old settings kept | `test_ft_opt_04_*` |
| FT-OPT-05 | Optional fields cleared | Stored as cleared | `test_ft_opt_05_*` |
| FT-OPT-06 | Reminder settings | Saved | `test_ft_opt_06_*` |
| FT-RESTART-01 | Restart before the wake-up | Starts as normal | `test_ft_restart_01_*` |
| FT-RESTART-02 | Restart after a start, heater running | No second start | `test_ft_restart_02_*` |
| FT-RESTART-03 | Restart after a start, heater turned off by hand, colder | No second start | `test_ft_restart_03_*` |
| FT-RESTART-04 | The start day is saved, and deleted with the integration | Saved; removed | `test_ft_restart_04_*` |
| FT-RESTART-05 | A corrupt saved start day | Ignored; starts as normal | `test_ft_restart_05_*` |
| FT-CLOCK-01 | Monday after the spring clock change | Starts at 06:30 local | `test_ft_clock_01_*` |
| FT-CLOCK-02 | Monday after the autumn clock change | Starts at 06:30 local | `test_ft_clock_02_*` |
| FT-CLOCK-03 | A 03:30 wake-up on the autumn change (hour happens twice) | One start | `test_ft_clock_03_*` |
| FT-CLOCK-04 | A 03:30 wake-up on the spring change (hour does not exist) | Still starts | `test_ft_clock_04_*` (fails: FT-3) |
| FT-CLOCK-05 | Host paused 06:00–09:00, wake-up fires late | No start | `test_ft_clock_05_*` |
| FT-CLOCK-06 | Wake-up 14 min late | Still starts | `test_ft_clock_06_*` |
| FT-CLOCK-07 | Wake-up 16 min late | No start; the result says why | `test_ft_clock_07_*` |
| FT-CAR-01 | Weekly car check timing | Sunday 18:00 only; never starts the heater | `test_ft_car_01_*` |
| FT-CAR-02 | The car answers | No notice, no message | `test_ft_car_02_*` |
| FT-CAR-03 | The car does not answer (looked 3 times) | Repair notice and message | `test_ft_car_03_*` |
| FT-CAR-04 | Only an old reading comes back | Counts as no answer | `test_ft_car_04_*` |
| FT-CAR-05 | It answers again the next week | Notice goes | `test_ft_car_05_*` |
| FT-CAR-06 | The heater runs | Notice goes | `test_ft_car_06_*` |
| FT-CAR-07 | The heater is running at check time | No request needed; OK | `test_ft_car_07_*` |
| FT-CAR-08 | Cloud down when asking | No verdict, no notice | `test_ft_car_08_*` |
| FT-CAR-09 | Cloud down after asking | No verdict, no notice | `test_ft_car_09_*` |
| FT-CAR-10 | Turned off in the options | Notice cleared; no more checks | `test_ft_car_10_*` |
| FT-CAR-11 | Unloaded while waiting for the answer | Gives up quietly | `test_ft_car_11_*` |
| FT-CAR-12 | Diagnostics | Show the last check | `test_ft_car_12_*` |

## Live check after each update (read-only)

Done through Home Assistant with no service calls, so nothing is turned on:

1. The integration is loaded, at the expected version.
2. All entities exist and none are unavailable.
3. The Schedule switch shows today's ready-by times and the pause date.
4. The learned sensors match the expected values.
5. The log has no errors from `custom_components.eberspaecher`.

## Results (5 Nov 2026)

First run: 363 passed, 3 expected failures (the findings below), stable over 10 repeated runs.
After the fixes in 0.6.0: 381 passed, 1 expected failure (FT-3), stable over repeated runs.

| ID | Finding | Severity | Status |
|---|---|---|---|
| FT-1 | The "already started today" mark is kept only in memory. After a restart, if the heater is no longer running (it was turned off by hand) and it got colder, a later wake-up starts it a second time that day. | Low | Fixed in 0.6.0: the start day is saved |
| FT-2 | A wake-up that fires late (the host was asleep or busy) still starts the heater, even after the car should have been ready. | Low | Fixed in 0.6.0: more than 15 min late is skipped |
| FT-3 | Home Assistant skips a time that does not exist. A start time between 03:00 and 03:59 on the last Sunday of March does not run. Only possible with a ready time before 05:40 on Sundays. | Very low | Platform behaviour, documented |
