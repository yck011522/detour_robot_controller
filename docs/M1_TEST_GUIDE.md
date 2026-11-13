# M1: controller, camera and admin diagnostics

Implementation date: 2026-11-09. The controller runs independently of the UR and
tool: no UR connections, motion, power commands or end-effector commands. It is
a bench profile, not the production startup/homing/zone/board controller.

## Run

Close other applications using the modem's configured COM port, then run from
the repository root:

```powershell
.\.venv\Scripts\python.exe -m game_controller

# Automated observation: stop-only regardless of joystick/keyboard input.
.\.venv\Scripts\python.exe -m game_controller --stop-only --seconds 30

# Optional console diagnostics only, without camera windows.
.\.venv\Scripts\python.exe -m game_controller --stop-only --no-video --seconds 10

# Small direct baseline if diagnosing a transport regression.
.\.venv\Scripts\python.exe tools/m1_connection_probe.py

# Hardware-free checks.
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Missing modem/gamepad at startup reports an error and quits. Modem reconnects
only to its configured COM port after runtime loss. No BLE adapter or car MAC
selection is needed. Four available uniquely mapped displays are required for
the normal four-view launch; `--no-video` isolates communication diagnostics.

The application starts in admin mode with all four cars Present. The admin
**Car Present** checkbox records reservation presence: checked means present.
It does not gate player or admin AUTO/MANUAL driving. M1 has no reservation
scheduler yet, so this flag currently records the operator's assertion only.
Initial values are configured in `config/game_config.ini`; disconnection never
changes the checkbox. Cameras retry independently. Existing feedback, echo,
device and protection checks still apply. Shutdown requires stop confirmation
from all four cars regardless of presence; unavailable cars are reported as
unconfirmed and make the run result FAIL.

## First supervised driving test

1. Verify Car 1 feedback and command echo are fresh, and its camera plays.
   The car can have fresh feedback while video is unavailable, or vice versa.
2. In admin mode, use the top overlay on any of the four displays. No fifth
   visible panel or driving-enable button exists. Input control is always
   available when fresh and eligible; `--stop-only` forcibly suppresses motion
   for automated observation.
3. **Space** toggles admin/player mode from any focused application window.
   All four overlays update even with no new camera frames. Admin mode ignores
   public joystick input; the selected admin car may still be driven. Other cars
   show **Paused by Admin** on grey. The selected car shows **Controlling by Admin**
   on dark orange, changing to dark red while Left Ctrl selects direct control.
   Shared modem/gamepad diagnostics repeat; each screen shows only its own car
   telemetry. Player mode hides the admin controls, retaining a compact health bar.
4. In player mode, test Car 1's forward/left/right buttons. M1 deliberately sends
   direct sanitized AUTO bench intent; it does not implement M2's tag-aware
   split/merge/ordinary-road steering filter. Both physical turn buttons mean
   neutral. Check raw input and effective command independently.
5. In admin mode, **1-4** select the car; **Up/Left/Right** drive AUTO. Hold
   **Left Ctrl** for MANUAL, with **Down** for reverse. MANUAL left/right alone
   pivot; AUTO turn bits alone do not enable forward speed. Presence does not
   block these commands; stale/protected cars remain stopped. A missing gamepad suppresses driving
   in both modes under the agreed device-loss policy.
6. **Escape** cancels current admin keyboard intent, not admin mode or exit.
   Held inputs are freshly sampled again; this is not a latched emergency stop.
   Release the controlling inputs to stop. Leaving admin/recovering devices
   resumes immediately from held input once fresh and eligible.
7. Test unplug/replug of the gamepad, car charging power-off/return and modem
   disconnect/reconnect in a supervised setup. Car feedback/echo must become
   stale and drive commands suppressed, then resume from fresh held input.
   No physical device-disconnect test was performed automatically.
8. **Q** or closing an application window opens a quit confirmation on all four
   screens and suppresses driving. **Enter** confirms; **Escape** cancels. No
   robot HOME requirement is claimed in M1. After confirmation the PC
   sends stop-only snapshots for the configured interval and checks fresh echoes
   plus reported wheel speeds. An unconfirmed Present-car stop is logged as
   FAIL; the application cannot independently verify physical rest. Production
   verified-HOME exit remains M4/M5 work, because M1 never owns the robot.

Admin key events work within the application's focused windows, not as a system
global keyboard hook. Focus loss expires keyboard intent; switching modes or
selection recomputes from current input rather than cached commands.

## Configuration

`config/address.ini` owns COM port, camera URLs, display indexes, gamepad button
mappings and admin key names. As confirmed by the operator, Car 1 is now
`rtsp://192.168.0.6/0`, Car 2 is `.5`; displays remain 2/3/0/1.

`config/game_config.ini` owns initial presence/admin state, display/UI
rates, telemetry/echo/video freshness thresholds, retry intervals and log folder.
The agreed 50 Hz control cadence is validated. Initial freshness defaults are
0.5 s feedback/echo, 1 s video and 0.15 s maximum unsent command age. These are
test settings, not measured end-to-end stopping guarantees. Driver baud/framing,
wire bit values, four logical car slots and the tested 1920x1080 canvas remain
protocol/renderer constants rather than gameplay tuning.

## Implementation boundaries

- `game_controller/app.py`: hidden SDL/HID event owner, 50 Hz state submission,
  shared admin state, ordered telemetry handling and shutdown. No fifth visible
  window and no robot ownership.
- `protocol.py`, `state.py`: frozen packet encoding and small testable M1 rules.
- `modem.py`: single serial owner, fixed driver settings, bounded ordered RX,
  latest-only writes with expiry and configured-port reconnect. It never replays
  a last driving snapshot indefinitely if the controller stalls.
- `display.py`, `video.py`: four SDL processes, independent decoder threads,
  cached YUV textures, independent overlay redraw and local camera retries.
  The native bridge was extracted from E9; experiments remain unchanged.
- `config.py`: device/behavior configuration validation.

Telemetry sequence/echo reset detection is heuristic because the wire protocol
has no session token. Repeated/out-of-order samples do not refresh driving
health; after a silence/new serial session, fresh local snapshot echoes are
required again. Intermediate distinct UIDs are not coalesced away; a queue
overflow fails the run rather than silently losing transitions. Full NFC
localization/token reconciliation remains M2/M3 and E16 work.

Logs in `logs/m1/*.log` are timestamped JSON lines: raw RX/TX, button changes,
command changes, admin/presence events, periodic timing/video/echo summaries,
shutdown stop confirmations and final result. Logs/captures are ignored by Git.
A run's PASS means that session completed and its required stops were observed;
it does not automatically sign off all M1 acceptance cases.

## Evidence so far

- Direct hardware probe: detected **Generic USB Joystick, 12 buttons**, opened
  COM11 and submitted 250 stop-only snapshots in five seconds. Car 1 feedback
  and progressing echoes observed. Initial reads may contain buffered telemetry;
  that probe's total count is not a steady-state telemetry-rate measurement.
- Sixteen hardware-free tests pass: published wire vector/offsets, strict parser,
  physical-input conflicts, stale/duplicate/reset/wrap handling, admin/held-input
  policy, firmware-protection gating, partial ordered serial input, no stale
  snapshot replay and fixed-port reconnect after a simulated disconnect.
  Overlay checks cover selected/paused/direct color states, hidden controls and
  removal of admin panel pixels in normal mode.
- Central-panel stop-only run completed with Car 1 stop confirmed.
- Four-screen **35 s and 45 s stop-only sessions** completed with Car 1 video,
  fresh feedback/echo, zero corrupt camera frames in sampled metrics and three
  independent offline-camera views. Control mean intervals were approximately
  20.06 / 20.01 ms; p95 approximately 20.37 / 20.38 ms. Startup skips were 3 / 1;
  maximum intervals included startup work and are not a hard real-time guarantee.
  Logs: `20261109_154046_818756.log`, `20261109_154233_592361.log`.
- Cross-window stop-only smoke verified four Space toggles, selection of cars
  2 then 1, and Escape cancellation through the player-window event path. Log
  `20261109_154429_479402.log`. These were targeted Windows key messages, not
  proof of all physical keyboard/focus-loss cases. Every transmitted movement
  byte in all automated live sessions was stopped AUTO (`0x01`); final Car 1
  stop receipt/reported wheels were confirmed. UR/tool were untouched.

### Operator follow-up and overlay revision, 2026-11-09

The operator reported successful car power-off/return. Log
`20261109_160617_806806.log` contains feedback gaps of 14.526 and 8.141 seconds,
then restored echo progress and a final confirmed stop. TX after the feedback
stale threshold during those gaps was checked to contain stopped AUTO only.
The subsequent approximately 11-minute session
`20261109_161105_479146.log` completed without recorded runtime/display errors;
sampled video corruption remained zero, final stop was confirmed, and the final
timing summary showed about 20.009 ms mean / 20.370 ms p95, with 8 skipped ticks
over the whole session. Small initial stale-sequence counts occur while draining
buffered startup telemetry; they did not continue growing during steady driving.

The revised UI passed a stop-only smoke with exactly four visible application
windows, admin hide/show, Q/Escape cancellation and Q/Enter confirmation. Log:
`20261109_163137_213231.log`. Every TX remained stop-only. The early failed smoke
`20261109_162845_396056.log` was interrupted by its test harness after detecting
the leftover standalone window and is not a pass; that window creation was then
removed. Future production HOME-gated quit remains deferred to M4/M5.

**M1 accepted and closed, 2026-11-09:** the operator confirmed the final physical
gamepad/modem reconnect and held-key/focus checks passed and requested closeout.
A one-car bench run can exercise controls, but shutdown still reports missing
stop confirmations for the other three cars independently of their presence.
Four-active-car/video load remains expanded coverage when hardware is available.

The STOPPED-but-receiving incident and suspected E15 post-NFC interlock are
reviewed in [M1_UNRESPONSIVE_CAR_REVIEW.md](M1_UNRESPONSIVE_CAR_REVIEW.md).

The final closeout assessment and evidence boundaries are in
[M1_AUDIT.md](../development_plan/M1_AUDIT.md). Final reconnect/focus checks are
operator-reported hardware results; no new log filename was supplied. Retain
this guide as a regression checklist for subsequent firmware/application changes.
