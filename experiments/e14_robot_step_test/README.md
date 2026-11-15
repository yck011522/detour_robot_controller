# E14: supervised robot operation tests

Supervised hardware experiments, separate from the game controller. A-D pickup
and placement and both directed home routes were exercised on 2026-11-15.
The three-plate session completed nine transfers and 162 actions, including
pause/resume and controlled quit. See [the validation note](VALIDATION_2026-11-15.md)
for coverage, numerical results and the 24-second B tightening outlier.
The commit-preparation cleanup has offline verification only. No game board
inventory, car firmware or reservation-manager behavior is changed.

| Entry point | Purpose |
|---|---|
| `step_test.py` | Read-only state or one supervised action |
| `zone_flow.py` | Approach, pickup or placement in A-D |
| `home_travel.py` | Directed travel between homes |
| `queue_cli.py` | Three-plate transfer queue with pause and controlled quit |
| `plot_test.py` | One action's PNG and CSV |
| `analyze_session.py` | Session audit, numerical CSV/JSON and overview PNG |

Use the root `requirements.txt` for runtime dependencies. Plot generation alone
also needs the optional `requirements-analysis.txt` in this folder. Offline
control/queue tests do not require matplotlib or any hardware connection.

## Shared settings

`config/robot_movement.ini` supplies joint speed/acceleration, arrival tolerance,
stationary threshold, settling, dwell, compliance speed/orientation limits,
forces, depths and timeouts for all four zones. [runtime] holds the existing
receive/log/loop timings, feedback/reply deadlines, watchdog, stop decelerations
and numeric payload-comparison tolerances. Their values are unchanged by cleanup.
**2026-11-16:** the joint speeds in that file were doubled to 10 deg/s (local) and
20 deg/s (cross-home) for the game controller. The validation below used 5 and 10
deg/s, so rerunning these experiments now runs at the doubled, unvalidated speeds.
The game controller has its own copy of these primitives in `game_controller/robot/`.
The tool forwarder port lives in config/address.ini under robotic_arm.tool_tcp_port.
Every action logs the effective settings. No application warning thresholds are introduced.

- Tightening: downward 10 N, fresh selected-motor stall completes. Stop on more
  than 20 mm downward travel or more than 30 seconds without timely completion.
- Release: loosen with XYZ compliance to +20 mm from the action's actual starting
  Z. At 30 seconds, +10 mm or more is accepted; less than +10 mm fails. Use the
  current displacement, never an earlier maximum. A successful fallback is
  recorded as `release_timeout_minimum_height_accepted` and proceeds normally.
- Placement release (M1): +5 N. Pickup release (M2): +15 N. The initial zero-force
  M1 release worked in B/D but timed out at ~6.45 mm in A; A's +5 N recovery
  reached +20 mm in ~4.73 seconds. The shared M1 default was then changed to +5 N.
- Communication, invalid feedback, controller safety/program changes, unexpected
  motor state and unconfirmed stop remain independent failures. Failure blocks
  subsequent steps; no automatic retry or protective-stop reset.
- Compliance starts before an engaged motor turns. Preserve payload and FT
  reference during tightening/release. Payload is changed only after release
  and stop, before retreat. Setting payload automatically re-zeroes FT readings.
- A cancelled or failed cleanup returns failure, preventing the next flow step.

## One action at a time

From the repository root (close other robot/tool control clients first):

```powershell
# Read-only: does not upload a control script or command the tool.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/step_test.py state

# Direct single joint leg, only from an operator-verified starting pose/path.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/step_test.py move --pose b_high --payload keep --live

# Individual engaged motor actions, preserving existing payload/reference.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/step_test.py tighten --motor 1 --live
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/step_test.py withdraw --motor 2 --live
```

These are choices, not a batch to run consecutively. `--force` and `--seconds`
remain explicit diagnostic overrides; otherwise values come from the INI.
`--config` selects an alternative settings file. The old generic 30 mm travel
bound has been removed in favor of the agreed directional tightening limit and
release policy. `loosen-compliant --motor 1 --payload keep --seconds 4 --live`
retains the earlier timed zero-wrench diagnostic; elapsed time does not prove
mechanical disengagement. `zero --payload keep --live` is a separate explicit
FT zero action for stationary, contact-free conditions only.

The tool-only/loaded payload options on MoveJ explicitly apply the corresponding
calibration in game_config.ini. `keep` issues no payload-setting or zero command.
Each active step requires typing RUN. Ctrl+C requests cleanup. Native calls can
delay Python interruption; keep pendant and independent tool-power stop available.
Tool firmware has no host-loss motor watchdog. Robot watchdog and finite tool
reply deadlines remain active during actions. No power/brake commands are issued.

## One runner for all zones

`zone_flow.py` supports A/B/C/D with their associated homes: A/C use ac_home;
B/D use bd_home. It does not automatically cross between homes. Preview is the
default and opens no hardware connections.

```powershell
# Offline preview of a complete pickup.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/zone_flow.py pick d

# Supervised complete placement or pickup, after checking the physical setup.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/zone_flow.py place b --live
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/zone_flow.py pick b --live

# Approach only: home -> high -> target, then stop for alignment inspection.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/zone_flow.py approach a --payload tool_with_floor --live

# Finish placement after operator-confirmed target alignment.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/zone_flow.py place a --from-target --live
```

A live flow requires RUN once, then checks fresh stationary starting joints,
robot mode and configured payload/CoG. Those checks do not establish physical
attachment or clearance; the operator confirms the physical setup. The flow
starts each child step deliberately, stops on any failure and uses the configured
0.5-second dwell between successful steps. Connections are recreated per step,
so there is additional preparation time beyond that dwell. Blending is not used.

## Directed home travel

`home_travel.py bd-to-ac --payload tool_with_floor` previews the route through
bd_ac_1..5 to ac_home. The reverse direction uses `ac-to-bd` and the distinct
ac_bd_1..5 route. Add `--live` for supervised execution and type RUN. Starting
home/payload checks and the shared step runner are reused. Each waypoint is a
stopped MoveJ using [cross_home] speed (10 deg/s commanded) and per-leg
timeout (120 s). Acceleration, settling and dwell remain from [joint_motion].
Local zone moves continue at their separate 5 deg/s setting. Both directed
routes, including loaded travel, were exercised with the 10 deg/s command
during queue testing. The runner passes a cross_home profile rather
than copying speed/timeout numbers into its command list.
Payload stays unchanged. No automatic shortcut or reversal is inferred.

The old place_b_flow.py, pick_b_flow.py, pick_d_flow.py and
place_d_from_target.py names are compatibility wrappers. They now preview by
default; pass --live to execute through the same shared runner. The separate
m2_free_spin.py is the earlier no-load motor diagnostic, not an engaged operation.

## Logs, plots and verification

Per-action JSON-line logs and flow transcripts are written under results/.
Logs distinguish target-height completion from minimum-height acceptance at
release timeout. Single-action/zone tests do not update game board inventory;
queue sessions persist their own software inventory, operator-confirmed at startup.

```powershell
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/plot_test.py PATH_TO_LOG
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/analyze_session.py PATH_TO_SESSION
# JSON/CSV-only review does not need matplotlib.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/analyze_session.py PATH_TO_SESSION --no-plot
.\.venv\Scripts\python.exe -m unittest discover -s experiments/e14_robot_step_test -p 'test_*.py' -v
```

Plots are PNG only, with a CSV export. Forces retain the raw base-frame external
reaction sign; commanded force is separately labeled. TCP/force samples remain
approximately 5 Hz, while motor replies are recorded more frequently.
New files under `results/` are ignored by Git and retained locally, including
checkpoints, input snapshots, latest-session pointers and generated reports.
Previously committed evidence remains tracked. Commit concise findings in the
validation note rather than adding new copies of machine/session state.

## Three-plate transfer queue CLI

`queue_cli.py` implements the experimental 12-directed-transfer coverage queue,
separate from production scheduling and car-zone reservations. Initial layout
is operator-confirmed through three plate-location questions. Offline preview
defaults to A/B/C occupied, D empty. Live startup detects ac_home or bd_home from fresh stationary
joint feedback using [queue_test].startup_home_tolerance_deg = 5 degrees per
joint, after the configured 0.2-second settle. It rejects no match or an ambiguous
match; it does not move/snap the robot. Normal move arrival tolerance remains
2 degrees. The first operation accepts the startup recognition tolerance;
later home checks use normal arrival tolerance.

Every live startup asks for the locations of all three plates (A/B/C/D or G for
gripper). --occupied provides visible prefills only. Saved --resume locations
must be physically confirmed again; a changed layout needs a fresh session.
A fresh held-plate start asks for its known pickup source and retains a pending
placement into the other empty slot. Initial plate_1/2/3 labels identify test plates by starting location,
not registered production board identities. All plates must match the calibrated
loaded payload. Keep cars outside the robot work area; this test has no tokens.

```powershell
# Offline preview of queue and first selection; no hardware connection.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py

# Live CLI: confirm physical layout and type RUN locally.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py --live

# Optional bounded acceptance run (default is unlimited until controlled stop).
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py --live --max-transfers 1

# Resume only from a controlled home stop, after confirming physical state again.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py --live --resume PATH_TO_STATE_JSON
```

P toggles the admin-like pause flag. The active sub-step finishes normally;
progression then waits before starting another sub-step. P again clears the
flag; toggling it off before the active step ends cancels the pending pause.
Motor-plus-compliance actions remain indivisible and end with tool/motion stop
and force-mode exit before pausing. No payload/tare change is introduced by a
pause. A paused process retains its operation, inventory and pending transfer;
it does not exit or automatically restart from another phase. The log records
PAUSED and PAUSE_ENDED. The UI reports PAUSE REQUESTED until the boundary is reached.

Q or Ctrl+C requests a controlled quit. The active pickup/place or
directed cross-home route finishes and returns to its associated home before
stopping. Quit overrides the pause flag: if paused away from home, the remaining
operation/retreat proceeds to home and then exits. If already paused at home,
quit exits without starting another operation. A pickup stop leaves the plate held and the transfer pending; a
placement stop leaves the tool empty and commits the completed transfer. A stop
during cross-home travel finishes that route without starting another pickup or
placement. Child processes are isolated from the console interrupt so Ctrl+C
requests a normal stop rather than interrupting the active child action.
For immediate equipment protection use the independent hardware stops; faults
still halt the operation and do not automatically retreat or resume.

Alternatively create a file named STOP inside the current session directory.
`results/latest_queue_session.txt` points to that directory. The CLI shows the
layout, gripper, queue, skipped entries, selected transfer, action progress and
completion counts. It does not retry failures. The queue entry moves to the back
only after placement and verified return home. An inventory transition is logged
after successful release, so a subsequent retreat fault retains that information.

Each session stores events.jsonl, state.json, transfers.csv and per-operation
transcripts referring to the usual action logs. Movement settings are copied
for the session; pose, payload, address and movement inputs are copied and
fingerprinted. Changing those inputs during a run blocks subsequent steps.
Fault/interrupted state files cannot be resumed automatically; reconcile the
physical mechanisms first. Controlled home stops can resume the held plate's
pending destination without selecting a new transfer. The production
config/board_state.ini is never changed. --confirmed skips only the final RUN
prompt; it does not bypass home detection or the three location questions.
