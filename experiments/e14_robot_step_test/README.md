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
| `c_pickup_check.py` | Isolated C pickup with high-point FT zero and shared +/-3 mm XY guard |
| `c_placement_check.py` | Confirmed-clear C pickup recovery and placement with UR-held XY |
| `c_cycle_check.py` | Full C pick/place cycle, XY held, +/-3 mm and 40 N combined lateral guards, JSONL/CSV |
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
is operator-confirmed through three plate-location questions. Live execution is the
default; use `--preview` for no hardware connections. Offline preview defaults to A/B/C occupied, D empty. Live startup detects ac_home or bd_home from fresh stationary
joint feedback after automatic power-on and brake release, using [queue_test].startup_home_tolerance_deg = 5 degrees per
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
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py --preview

# Live CLI: confirm physical layout and type RUN locally.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py

# Optional bounded acceptance run (default is unlimited until controlled stop).
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py --max-transfers 1

# Resume only from a controlled home stop, after confirming physical state again.
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/queue_cli.py --resume PATH_TO_STATE_JSON
```

On exit (including startup cancellation and faults), the CLI sends Dashboard
`stop` and `power off`, then verifies stable `POWER_OFF`. This engages the arm
brakes and powers off the arm only; the controller remains on. Cleanup failures
are reported and require checking the pendant. Forced process termination or
loss of connection can prevent cleanup. Startup requires Remote Control, NORMAL
safety, and no running program; no safety-stop reset is attempted.

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

## Isolated C pickup / high-point force zero check

Preview (no hardware connection):

```powershell
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/c_pickup_check.py
```

Supervised test, after stopping the game:

```powershell
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/c_pickup_check.py --live
```

Requires a stationary, powered/brake-released arm at `ac_home`, an empty tool
with the configured `tool_only` payload, and a floor installed at C. Verify the
C-high/target paths and that the tool is free of contact at C-high. The script
checks home and payload, then asks for RUN. It does not power the arm on/off.

Sequence: C-high -> stationary FT zero -> C-target -> M1 tightening -> M2 upward
withdrawal -> loaded-payload update and C-high retreat -> AC-home. The existing
separate-action control programs, forces, speeds, Z criteria and dwell are kept.
The explicit zero preserves payload and happens only before contact; there is
no zero between tightening and withdrawal. Before/after-zero force readings and
shared-reference XY displacement are recorded under `logs/robot/`.

XYZ remains compliant. A software guard stops the force step if either X or Y
exceeds +/-3 mm from the actual C-target TCP captured before tightening. That
same reference is retained through withdrawal; the allowance does not reset.
This is a sampled stop boundary, not position clamping or automatic recentering;
communication latency and deceleration can cause overshoot. Faults/Ctrl+C stop
the active action and prevent all later steps; no automatic retreat is attempted.

Success leaves the C floor HELD at AC-home. The game inventory file is not
updated: physically confirm the new layout before restarting the game. Reset
the mechanism and tool-only payload before repeating this pickup. An optional
`--skip-zero` comparison run retains the identical XY guard; first try the normal
zero-enabled test. Stop and investigate a guard failure rather than widening it.
The guard/confirmation hooks in the shared action runner are inactive in the game.

## C placement check with XY held

`c_placement_check.py` is specifically for the stopped C pickup whose floor the
operator has confirmed is attached and clear of the structure. It requires the
old tool-only payload and a stationary pose near C-target. It is not a general
recovery path from arbitrary positions. Preview with no arguments; `--live`
requires RUN and performs the confirmed-clear position-controlled lift to C-high.

At C-high, it applies the loaded payload and zeros FT while stationary/free of
contact. The approach, M2 tightening, M1 withdrawal and retreat share one +/-3 mm
XY software guard. During the force actions UR selection `[0,0,1,0,0,0]` holds
X/Y and makes only Z compliant; the noncompliant X/Y deviation settings are
0.003 m, with Z speed unchanged. This removes lateral self-alignment. These
options are experimental and are not enabled in the game controller.

After successful release, the tool returns to C-high with the loaded reference
preserved, then switches to tool-only payload and zeros at high. It returns empty
to AC-home. Faults stop progression without retry/home. No game inventory changes
or power/brake commands are sent. Logs go to `logs/robot/`.

Live result on 2026-11-18: placement completed and returned to AC-home. The shared
reference saw maximum absolute X/Y excursions of approximately 0.360/0.125 mm
across approach, force actions and retreat. M1 withdrawal reached approximately
20.49 mm in 4.18 s of sampled action time. Holding X/Y also produced lateral
Y readings up to approximately 26.7 N during M2 tightening, so reduced drift
alone does not establish correct mechanical alignment. Payload updates and FT
zeroing were performed at C-high before placement and after empty-tool retreat.
Report: `logs/robot/20261118_140642_979052_c_placement_check.jsonl`.

## Full C pickup/placement cycle with 40 N lateral guard

Preview: `python experiments/e14_robot_step_test/c_cycle_check.py`.
Add `--live` and confirm RUN for one supervised cycle from stationary AC-home
with an empty tool, tool-only payload, C floor installed and the game stopped.
It picks C, returns loaded to AC-home, places C, then returns empty to AC-home.
X/Y are held by UR during force actions; only Z is compliant. Each pickup/place
contact phase shares one +/-3 mm XY reference across approach, tightening,
withdrawal and retreat. A separate sampled guard stops if `hypot(Fx,Fy) > 40 N`.
This is a stop threshold, not a commanded lateral force or certified safety limit.
Payloads and FT zeroes change only at stationary, contact-free C-high; existing
force magnitudes, speeds and Z criteria remain. Failures stop without retry/home.

The script writes JSONL and CSV samples to `logs/robot/` with step names, controller
timestamps, TCP XYZ, XY displacements, force XYZ and combined lateral force.
Live cycle on 2026-11-18 14:14:49-14:16:52 completed with neither guard tripping:

| Phase | Peak combined lateral force | Maximum absolute X/Y excursion | Upward release |
|---|---:|---:|---:|
| Pickup C | 26.91 N | 0.364 / 0.122 mm | 20.15 mm in 4.54 s |
| Place C | 24.62 N | 0.370 / 0.139 mm | 20.08 mm in 4.19 s |

The final live check verified stationary AC-home, NORMAL/RUNNING and tool-only
payload; arm power was left on. The cycle did not update game inventory.
Data: `logs/robot/20261118_141449_892167_c_cycle_check.jsonl` and matching `.csv`;
per-step statistics are in the matching `.summary.json`.

## Zone A using the same guarded cycle

The shared cycle runner now accepts `--zone a` or `--zone c` (default C).
Example preview: `python experiments/e14_robot_step_test/c_cycle_check.py --zone a`.
Add `--live` for the supervised cycle. Each selected zone uses its own taught
high/target poses, payload/tare at that high pose, and zone-labelled JSONL/CSV
files. A and C both start/finish at AC-home; B/D are not accepted by this runner.

Live A attempt on 2026-11-18 14:32:00-14:32:34 stopped during M1 pickup tightening
when combined lateral force reached 41.47 N, exceeding the 40 N threshold.
At that instant Fx/Fy were approximately -21.28/+35.60 N. Tightening X/Y excursions
remained below 0.19 mm, while target approach had measured up to 61.83 N absolute
Z force. M1 tightening was incomplete; M2 withdrawal and all placement steps were
not executed. Tool/motion/force/program cleanup completed; the arm was left
stationary near A-target with tool-only payload for physical inspection. No
retry, retreat or power-off was performed. These readings suggest checking
contact/alignment before increasing the threshold; holding XY does not remove
mechanical preload.
Data: `logs/robot/20261118_143200_358075_a_cycle_check.jsonl` and matching `.csv`;
per-step statistics are in the matching `.summary.json`.

## A and C compliance teaching settings for later production integration

At the placement M2 stall, record the actual joints and TCP pose as the tightened pose. For the approach target, add 0.005 m to base Z, preserve orientation, and request UR inverse kinematics with the stall joints as the seed.

- Use MoveJ to the approach target with stops at 80 N absolute Fz or 40 N resultant lateral force.
- Tighten M1 for pickup or M2 for placement with 10 N downward, XYZ compliant, 5 mm/s translation limit and 2 degree orientation deviation. Finish on stall and retain the existing timeout and 20 mm downward travel stop.
- Withdraw by loosening M2 for pickup or M1 for placement with 15 N upward, only Z compliant, XY position held, and a 3 mm per-axis XY guard. Measure 20 mm upward from the release start. The validation trials required the full distance even at timeout. The 5 mm teaching offset applies only to the approach pose.
- Set and verify payload and tare stationary at zone-high, then preserve the reference through contact. Tool-only mass is 1.19 kg and tool with floor is 2.11 kg. Keep the existing separate action sessions and cleanup.
- The C validation used 80 N absolute Z and 40 N lateral stops on withdrawal as well as on MoveJ. The A pickup was split into two withdrawals. The C validation completed both withdrawals uninterrupted.

C teaching and validation report is in logs/robot/20261118_170026_792288_c_teaching.jsonl. Production integration is deferred.

### Validation results on the taught targets, 18 November 2026

| Zone | Pickup withdrawal | Placement withdrawal |
| --- | --- | --- |
| A | Split across two actions, with 15.27 mm in the second | Reached 20 mm |
| C | 20.03 mm | 20.16 mm |
| D | 20.16 mm | 20.18 mm |
| B | 20.17 mm | 20.15 mm |

C, D and B reached full withdrawal in a single action in each direction. The D board finished at B, D empty, with the arm stationary at BD-home. The B/D report is logs/robot/20261118_171752_365310_bd_teaching.jsonl. Game inventory was outside the experiment.
