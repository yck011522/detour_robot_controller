# E14: supervised robot operation tests

Temporary hardware experiments, separate from the game controller. B/D placement
and pickup were physically confirmed on 2026-11-15 before this refactor. The
second cleanup has offline verification only. Before this cleanup, B/D/A pickup
and placement, C placement, loaded BD->AC at 5 deg/s and empty AC->BD at 10 deg/s
were confirmed. C pickup, loaded transfers at 10 deg/s, and B/D placement with
the newer 5 N M1 release remain untested. No game board inventory is changed.

## Shared settings

`config/robot_movement.ini` supplies joint speed/acceleration, arrival tolerance,
stationary threshold, settling, dwell, compliance speed/orientation limits,
forces, depths and timeouts for all four zones. [runtime] holds the existing
receive/log/loop timings, feedback/reply deadlines, watchdog, stop decelerations
and numeric payload-comparison tolerances. Their values are unchanged by cleanup.
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
stopped MoveJ using [cross_home] speed (10 deg/s for the next test) and per-leg
timeout (120 s). Acceleration, settling and dwell remain from [joint_motion].
Local zone moves continue at their separate 5 deg/s setting. Empty AC->BD was
verified at 10 deg/s; loaded BD->AC was verified at 5 deg/s. Loaded 10 deg/s
transfers remain to be tested. The runner passes a cross_home profile rather
than copying speed/timeout numbers into its command list.
Payload stays unchanged. No automatic shortcut or reversal is inferred.

The old place_b_flow.py, pick_b_flow.py, pick_d_flow.py and
place_d_from_target.py names are compatibility wrappers. They now preview by
default; pass --live to execute through the same shared runner. The separate
m2_free_spin.py is the earlier no-load motor diagnostic, not an engaged operation.

## Logs, plots and verification

Per-action JSON-line logs and flow transcripts are written under results/.
Logs distinguish target-height completion from minimum-height acceptance at
release timeout. No board identity or attachment state is inferred or persisted.

```powershell
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/plot_test.py PATH_TO_LOG
.\.venv\Scripts\python.exe -m unittest discover -s experiments/e14_robot_step_test -p 'test_*.py' -v
```

Plots are PNG only, with a CSV export. Forces retain the raw base-frame external
reaction sign; commanded force is separately labeled. TCP/force samples remain
approximately 5 Hz, while motor replies are recorded more frequently.

## Next phase: three-floor queue CLI (not implemented)

The next reliability test should use three active floors and the agreed queue
policy, display selection/eligibility/phase decisions in a CLI, and run until
the operator requests a controlled stop. That stop should finish the active
operation and return to either home, with or without a floor, before exiting.
It must be distinct from an immediate fault/abort stop. No repeat runner,
scheduler, queue UI, or controlled-stop loop is implemented in this cleanup.
The last confirmed physical arrangement is empty tool at bd_home, one test
floor in C; operator setup of the three-floor layout will be needed later.
