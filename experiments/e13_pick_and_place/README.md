# E13: supervised pick-and-place with compliant screw engagement

Status: implemented with offline tests; combined live routine NOT yet tested.
E12's successful motion test and E11's tool test are the baselines.
This experiment is separate from the game-controller milestones.

## Run

```powershell
# Default: no hardware access; simulated action completion in one second.
.\.venv\Scripts\python.exe experiments/e13_pick_and_place/pick_and_place.py

# Enable Connect; launching alone does not connect or move anything.
.\.venv\Scripts\python.exe experiments/e13_pick_and_place/pick_and_place.py --live

# Offline verification; no robot/tool access.
.\.venv\Scripts\python.exe -m unittest discover -s experiments/e13_pick_and_place -p 'test_*.py' -v
```

For the normal cycle, start at taught HOME with the floor already held, M2 disengaged, correct TCP,
Remote Control, NORMAL safety and brakes released. Outside HOME the cycle is
blocked but the direct HOME button is available. Close other robot/tool clients. E13 does not power
up/down, reset protective stops, configure the forwarder or automatically home.
The existing robot RS485 TCP forwarder on port 54321 must be working.

Connect checks the robot and tool, verifies taught FK/LOW IK and controller
limits, applies tool-with-floor payload, and zeroes FT at stationary HOME.
HOME must be free of external contact for that zeroing. Connect itself does
not tighten a motor or send a trajectory. Tick the clearance confirmation,
then hold FORWARD for the initial M1 stall confirmation. The initial check
holds the robot stationary; it does not enable compliance at HOME.

Hardware calls run in a separate process so a native library stall cannot freeze
the UI. The worker checks sender-timestamped UI heartbeats (250 ms), robot
feedback freshness (200 ms), and a 3 Hz robot communication watchdog. Tool
replies have a 120 ms deadline. These are fault deadlines, not tightening
timeouts. Tool firmware has no host-loss motor watchdog: independent tool
power interruption must remain available if the PC or communications fail.
Release handling has communication/OS latency and robot deceleration; it is
not an instantaneous or certified safety stop.

Runtime JSON-line logs go to results/*.log. Only live hardware confirmation
can establish mechanical success; preview does not simulate force physics.

### Temporary hand-held fixture test and direct HOME

`CHECK_XY_DRIFT = False` temporarily disables the X/Y displacement fault, as
requested for a hand-held receiving structure. It does not disable compliance
or translational speed limits. Z overtravel and orientation checks stay active.
Restore this flag to True when testing against the fixed fixture.

For a direct return, release the current button, confirm **Tool disengaged;
HOME path clear**, then hold **moveJ HOME**. The main clearance checkbox must
also be checked. Tool STOP and robot stop precede replacement of a running
operation; HOME uses moveJ at 5 degrees/s. Release/focus loss stops that move,
and holding HOME again resumes it. This is a direct joint path, bypassing
HIGH/LOW; it does not unscrew or detach anything automatically.

Direct HOME abandons the routine and blocks normal forward/reverse actions.
Restart with the expected initial carried-floor setup before running the cycle
again. The payload already selected in the session is retained for the direct
return. Action faults retain the worker, step and connections for Retry/Skip.
If the worker has exited, Connect starts a fresh session with the initial
tool-with-floor payload. A fresh
connection outside HOME permits only direct HOME. Do not reconnect as a way to
infer or repair attachment state.

## Operator controls

### Recover a red state

**Retry step** retries STOP, verifies tool IDLE/PWM=0 and fresh stationary robot
feedback, and restores the control script if stopped. It prepares the same
action with fresh stall observations and travel bounds. Release, then hold the
original direction to retry. No trajectory is sent by the Retry button.

**Skip step** performs the same recovery checks, then records an operator
override and advances the selected action to its destination state, applying
its payload transition. Height, stall and attachment are not reported as
measured success: the operator assumes that physical endpoint before choosing
the next held action. Neither button automatically starts the next step.

Both controls can stop a running operation or recover an action fault. Failed
recovery stays red with its reason and may be retried. Missing feedback/tool
replies, actual motion or non-NORMAL safety must be resolved before motion
continues. No protective-stop reset or power cycling is performed.

The observed log reported `Robot mode/safety/program changed` during stop while
the tool reported IDLE/IDLE/PWM=0. Stop confirmation had reused the driving check,
which required a running control script. Robot stop confirmation now uses fresh,
finite joint velocities independently of script-running state. This fixes the
diagnostic coupling; the cause of the control-program change is not established.

- Hold FORWARD to execute the next operation. Release, leave the button, lose
  window focus or press Escape to stop robot and tool. Exit force mode and
  confirm the robot stopped; do not leave a downward/upward force active.
- Each completed operation turns the active button green and stops progression.
  A mouse release followed by a new press is required to begin the next operation;
  continuously holding never starts the next step.
- A paused operation retains its destination, motor stall observations and
  attachment state. Re-pressing resumes the unfinished operation, not the next one.
- REVERSE requests the explicitly defined inverse operation. Robot-only legs
  retrace their current segment. Tool steps cannot be reversed by simply
  decrementing the step index: first restore the required mechanical support.
  Interrupted joint legs can reverse toward their starting endpoint. For this
  first version, a paused tool/force action must finish in the same direction
  before changing direction; the UI says so rather than guessing thread state.
- Completion, paused, running and fault states need distinct colors and labels.
  A fault stops progression and shows its cause; Retry/Skip require explicit
  operator action and do not start motion automatically.
- Tightening ends on a fresh reported motor stall, with no tightening timeout.
  Communication-loss detection and operator release remain independent stop
  conditions. Record the stall before STOP clears firmware stall flags.

## Forward cycle

HOME, HIGH and TARGET are the taught E12 positions. LOW is TARGET plus 10 mm
along base Z, with the same orientation. All force directions below are base-frame
directions; X/Y compliance means zero commanded lateral force, not a programmed
lateral search. Hold orientation throughout force operations.

| State transition | Operation | Completion |
| --- | --- | --- |
| -1 to 0 | At HOME, tighten M1 | Fresh M1 stall |
| 0 to 1 to 2 | HOME to HIGH to LOW, fresh press per leg | Joint arrival, async move finished, stationary |
| 2 to 3 | Descend and tighten M2 simultaneously | Fresh M2 stall (no Z hold); then tool-only payload |
| 3 to 4 | Loosen M1 and withdraw simultaneously | LOW Z |
| 4 to 5 to 6 | LOW to HIGH to HOME, fresh press per leg | Joint arrival, async move finished, stationary |
| 6 to 7 to 8 | HOME to HIGH to LOW, fresh press per leg | Joint arrival, async move finished, stationary |
| 8 to 9 | Descend and tighten M1 simultaneously | Fresh M1 stall (no Z hold); then tool-with-floor payload |
| 9 to 10 | Loosen M2 and withdraw simultaneously | LOW Z |
| 10 to 11 to 12 | LOW to HIGH to HOME, fresh press per leg | Joint arrival, async move finished, stationary |

The initial carried-floor payload is TOOL WITH FLOOR, established before travel.
Both payload transitions above are explicitly operator-selected conventions; physical
load transfer can be gradual while the floor and structure share support.
Do not zero the force sensor against an external contact to hide that load.

## Force/position transition

During descent, selection [1,1,1,0,0,0] permits translation under the requested
downward force. Continue Z-compliant descent until the tightening motor reports
STALLED (M2 for placement, M1 for pickup), then stop both robot and tool and
apply the payload transition. TARGET Z is a reference for travel bounds, not
a completion requirement. No Z-hold moveL or 0.5 mm hold-drift fault remains.
A stall before TARGET is also completion. The 10 mm overtravel margin remains
active, along with orientation, feedback, heartbeat and motor-state checks.

Experiment-local constants at the top of the script: force 10 N, translational
force speed limit 5 mm/s, XY drift threshold 10 mm (temporarily DISABLED), Z overtravel 10 mm, withdrawal Z arrival
tolerance 0.5 mm, and orientation drift limit 3 degrees. Travel bounds remain
relative to the original action start across pauses. MoveJ remains 5 degrees/s.
Controller force-mode speed limits are not absolute travel bounds; the worker
separately monitors Z displacement and, when enabled, X/Y displacement. These values need checking against the fixture
before live use; exceeding bounds faults the session.

## Concurrent loosening and withdrawal

For withdrawal transitions 3 to 4 and 9 to 10, apply +10 N along base Z, with zero requested X/Y force
and orientation held. Keep the relevant motor loosening while the operator
holds the button. Actual TCP feedback reaching LOW Z within the chosen tolerance
is the agreed release completion criterion: no extra disengagement sensor,
operator confirmation or fixed loosening duration is required.

On reaching LOW Z, STOP the tool motor, exit force mode and stop/hold the robot;
confirm both have stopped before marking the operation complete and turning
the button green. Require release and a fresh press before the next movement.
If the operator releases early, stop both and retain the incomplete step;
re-pressing resumes loosening and upward withdrawal toward the same LOW Z.
Communication faults stop the operation rather than declaring completion.
This criterion uses height as a proxy for release, as agreed for this fixture;
it is not independent measurement of thread disengagement.

## Payload calibration

Read config/game_config.ini, sections robot_payload_tool_only and
robot_payload_tool_with_floor. Mass is kilograms; CoG entries are metres in
the tool-flange frame (UR payload convention).

| State | Mass kg | CoG mm (x,y,z) |
| --- | --- | --- |
| Tool only | 1.190 | -3,44,0 |
| Tool with floor | 2.110 | -1,35,26 |

## Reverse and acceptance

Reverse uses explicit physical transitions. From the installed target state it
loosens M2 while withdrawing; from an empty LOW it descends and tightens M1
before uninstalling. From the pickup-gripped TARGET it loosens M1 and withdraws
directly to the empty LOW state, undoing both grip and approach. From LOW with
the retrieved floor it can descend and tighten M2 to reinstall. Motor release
steps always use the agreed LOW-Z completion criterion. Payload changes occur
before withdrawal and after successful grip/fasten as required by each action.

Eleven offline tests passed, including an injected worker fault followed by
Retry, operator Skip and continued stepping, along with the earlier headless Pygame/Windows child-process
startup-and-shutdown check. Tests cover sequence endpoints, payload transition declarations,
Z-first and stall-first completion, retained stall across resume, withdrawal
height completion, travel bounds, unexpected loosening stall, and the real
preview worker's fresh-click gate. Live acceptance remains pending: verify
release/focus stop, partial joint reversal, each force/Z-hold transition,
motor stop, payload changes, and forward/reverse mechanical attachment with
the operator observing. Do not treat a passed offline test as loaded acceptance.

Use E10/E11's working robot RS485 forwarding connection. No firmware, power
state or forwarding configuration is changed by this document. No robot or tool
commands were sent while preparing it.

## Payload debugging ? 2026-11-09

On the connected stationary robot (ur_rtde 1.6.5), nine isolated setPayload
calls succeeded with mass/CoG feedback matching configuration. Six further
cycles through Hardware.halt followed by payload also succeeded. Original
2.110 kg / [-0.001, 0.035, 0.026] m payload was restored and the test control
script stopped. No trajectories, force-mode activation or tool commands were
sent in these checks.

The previous halt order called forceModeStop before cancelling the asynchronous
Z-hold moveL. The library's forceModeStop runs stopl internally, which can
conflict with a live move thread. Halt now performs a synchronous motion stop
before forceModeStop and velocity confirmation, then allows payload changes.
Twelve offline tests pass, including a regression for this ownership order.
The combined transition from active force/Z-hold still needs live verification
in the supervised routine; stationary payload tests do not reproduce it.

## Pickup and fault handling update

Pickup now uses the same concurrent descent/tightening behavior as placement.
The final state is 12 (previously 13). Restart the app; old session state indices
must not be reused. Fifteen offline tests pass. These changes have not been
run on the connected robot.

On 2026-11-09 at 22:41:08.561 +08:00, the previous routine logged Z overtravel
during uninstall. It did not log actual Z or force, so the direction and mechanical
cause cannot be reconstructed. Fault waiting previously skipped watchdog kicks,
which can cause the subsequent C207A0 protective stop. Fault waiting now keeps
RTDE alive while progression stays blocked; this does not reset a controller
protective stop. Retry also services the watchdog while checking stationary feedback.
Z_OVERTRAVEL now records actual/start/target/bound heights and TCP force.
The Z overtravel margin is now 10 mm on either side of the start-to-target interval. See [checks and errors](CHECKS_AND_ERRORS.md).


## Live operation diagnostics

The expanded UI shows the current action's measured values and requirements:

- Joint moves: six actual/target joint angles, maximum error versus 0.25 degrees,
  maximum joint speed versus 0.005 rad/s, and async running flag versus zero.
- Descent/tightening: actual/reference Z and error, explicit stall-only completion,
  motor state/current/PWM and stall latch (0 or 1; required 1).
- Withdrawal: actual/target Z and error versus 0.5 mm arrival tolerance.
- Force actions: requested/measured Fz, 5 mm/s commanded speed cap, actual Z versus
  allowed travel interval, orientation drift versus 3 degrees, XY drift versus
  10 mm with the current DISABLED status.
- Initial grip: position/orientation drift versus 1 mm / 1 degree and stall latch.
- Runtime: heartbeat age versus 250 ms, feedback age versus 200 ms, watchdog rate.
- Stop/recovery: actual joint speed, required three stationary samples and remaining
  time versus the three-second stop or two-second recovery deadline.

OPERATION_SAMPLE / JOINT_SAMPLE record measurements every 200 ms. Values remain
as the last sampled snapshot while paused/faulted. They are not refreshed live
in that state. Current/PWM are motor observations, not completion thresholds.
Force requests are +/-10 N; measured force is not a separate completion gate.
Tightening runs until the relevant motor stalls, with no Z hold. The 10 mm
travel margin remains active. Sixteen offline tests pass; this update has not
been exercised on the connected robot.

Numerical log coverage: PREPARED includes runtime thresholds and waypoints;
OPERATION_SAMPLE/JOINT_SAMPLE include measurements and their limits (5 Hz);
STOP_SAMPLE/RECOVERY_SAMPLE include velocity, sample counts and deadlines.
FAULT_SNAPSHOT takes a best-effort immediate read before fault cleanup of TCP
pose/force/speed, joints/velocities, timestamp, robot/safety mode and payload,
plus the selected action's start/target. Missing readings get per-field errors
without preventing STOP. Existing logs are unchanged; restart for this schema.

Current approach/withdrawal settings: LOW = TARGET +10 mm base Z; compliant translation speed cap 5 mm/s; force +/-10 N. See [drift diagnosis and next-run logging](DRIFT_DIAGNOSIS.md). E12 shares the LOW offset. Eighteen offline tests pass.
