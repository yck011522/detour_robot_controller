# E12: read-only robot teaching

First step: manually position the robot using the pendant, hold it still, and
run this minimal reader to copy its current joint configuration and TCP pose:

```powershell
.\.venv\Scripts\python.exe experiments/e12_robot_teaching/read_position.py
```

The script reads `[robotic_arm] host` from `config/address.ini`, opens only
`RTDEReceiveInterface`, prints once and disconnects. No control script, motion,
power/brake, freedrive or tool commands are sent. No positions/configuration
files are saved or overwritten; rerun it at each taught position.

RTDE port 30004 supports monitoring in both Local and Remote Control, so Local
Control is suitable while teaching with the pendant. The controller must be
reachable and RTDE enabled. See [UR's interface guidance](https://www.universal-robots.com/articles/ur/interface-communication/remote-control-via-tcpip/).

Outputs:

- Six actual joint positions in radians (for later moveJ/FK) and degrees
  (for comparison with the pendant).
- Actual configured TCP pose `[x,y,z,rx,ry,rz]`: XYZ in metres relative to the
  robot base; orientation is an axis-angle rotation vector in radians, not Euler
  angles. XYZ is also printed in millimetres for convenience.
- Controller timestamp and raw robot/safety modes for context.

Joint and TCP getters are separate reads, not a claimed atomic sample; teach
while stationary. The configured TCP offset and calibration matter: the reported
pose is the active TCP, not necessarily the flange. Keep the intended TCP setup
consistent during teaching and later FK/motion.

## Hold-to-run test UI

`hold_to_run.py` contains the operator-supplied TARGET, HIGH and HOME joint
configurations and TCP recordings. Test speeds/offsets are constants at the top
of that file, as requested; no new INI settings or firmware changes.

```powershell
# UI preview: no robot connection; live controls remain disabled.
.\.venv\Scripts\python.exe experiments/e12_robot_teaching/hold_to_run.py

# Enable explicit live connection in the UI. No trajectory starts on launch.
.\.venv\Scripts\python.exe experiments/e12_robot_teaching/hold_to_run.py --live
```

For movement, switch the robot to **Remote Control**, powered/brakes released,
NORMAL safety, stationary and with no other robot program running. Unlike the
read-only reader, the motion UI needs Remote Control. It does not perform those
startup operations or reset a protective stop for you. Close other control
clients before connecting.

1. Begin stationary. If outside the taught points, normal route controls remain
   blocked, but **HOLD GO HOME (initial approach)** is available after connection
   and clearance confirmation. It makes a direct moveJ at 5 degrees/s to HOME;
   verify that entire joint-motion path first. Release/focus loss stops it.
   Hold again to resume; after arrival release and use the normal route buttons.
   This button is only for the initial unrecognized position. Once on the route,
   use **toward HOME** to preserve the LOW/TARGET linear withdrawal.
2. Click **Connect live control (no movement)**. This uploads the ur_rtde control
   script and computes calibrated FK with the active TCP. It checks FK against
   all three recorded TCP poses, derives LOW at TARGET base Z +0.020 m, and finds
   LOW's joints with IK near TARGET. Controller limit checks and a branch-distance
   check must pass. They are not collision/path-clearance checks.
3. Verify the full robot/tool/board swept paths and the intended TCP/payload,
   then tick the clearance confirmation. No end-effector motors are operated.
4. Press and hold **toward TARGET** for HOME -> HIGH -> LOW -> TARGET. First two
   legs use moveJ; LOW -> TARGET uses moveL. With the button still held, the
   script advances through waypoints without blending.
5. Hold **toward HOME** to reverse: TARGET -> LOW uses moveL; LOW -> HIGH -> HOME
   use moveJ. An interrupted segment is remembered, so reversing first returns
   along that segment rather than skipping to another waypoint.
6. Release the mouse, leave the button, lose focus, untick confirmation or press
   Escape to request stopJ/stopL with the segment's matching stop type. After a
   stop, release then press again. Closing the window stops control; it does not
   execute an automatic return-home trajectory or power off the robot.

Defaults: moveJ **5 degrees/s**, acceleration **10 degrees/s²**; moveL
**5 mm/s**, acceleration **20 mm/s²**. Stop decelerations are 1 rad/s² and
0.10 m/s². LOW's recorded-pose estimate is `[-0.60249278, 0.68437515,
0.19139757, -1.41104056, -2.80687476, -0.00000969]`; the live motion goal is
derived from calibrated FK, not this illustrative rounded value.

The worker uses asynchronous move commands so release can issue stop, observes
joint speeds while stopping, and rejects stale feedback/mode changes. The UI sends
a heartbeat; a stale UI faults the session, and a 5 Hz robot-side ur_rtde
watchdog is configured as an additional communication-loss stop mechanism.
These are software controls, **not a certified hold-to-run/enabling device**.
Stopping involves deceleration and communication/OS latency; keep the pendant
stop available. Faults require resolving the issue and restarting the UI; no
automatic motion retry or reconnection resumes an interrupted trajectory.

Each live connection writes JSON-line records under `results/*_hold.log` for
calculated poses/joints/TCP, requested motion, stops, arrivals and faults.
No physical motion test was performed while creating this UI. Nine offline
tests passed, including fake-RTDE worker checks that connecting sends no move
and releasing joint/linear holds requests stopJ/stopL respectively.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s experiments/e12_robot_teaching -p 'test_*.py' -v
```

References: [ur_rtde asynchronous move/stop example](https://sdurobotics.gitlab.io/ur_rtde/pages/examples/basic_motion/move_async_example.html),
[control-script/watchdog implementation](https://gitlab.com/sdurobotics/ur_rtde/-/blob/master/scripts/rtde_control.script).

## Teaching/motion progression

### Preparation diagnosis (2026-11-09; no motion)

Live testing confirmed script upload succeeds. The subsequent FK call stalled
with an explicit all-zero TCP; omitting the TCP could instead return incorrect
poses. The upstream `getForwardKinematics` implementation selects a six-double
input recipe for zero/omitted TCP while the robot script reads twelve inputs.
On the installed ur_rtde 1.6.5 setup, explicitly passing a calculation-only TCP
with a 1e-12 metre X offset avoids that branch. This does not set/change the
robot's active TCP; the numerical difference is one picometre.

The corrected actual preparation worker was run with motion methods blocked.
HOME/HIGH/TARGET FK matched the taught values; LOW IK and all requested joint
and Cartesian controller-limit queries passed. It then correctly rejected the
current starting position: nearest HOME differed by 0.529 degrees against the
unchanged 0.250 degree limit during that diagnostic run. The later initial HOME
button revision replaces that startup rejection with an explicit hold-to-run
approach, while retaining the arrival tolerance. No trajectory, power-off or automatic
home command was sent. The diagnostic control program was stopped on exit.
See `results/20261109_182134_hold.log` for that run.

Preparation now reports and logs each individual check instead of leaving the
upload message visible throughout preparation. Controller-limit checks do not
validate collision clearance of the swept path.

Source: [ur_rtde FK implementation](https://gitlab.com/sdurobotics/ur_rtde/-/blob/master/src/rtde_control_interface.cpp)
and [robot-side FK command](https://gitlab.com/sdurobotics/ur_rtde/-/blob/master/scripts/rtde_control.script).

1. Teach Zone 1 TARGET and retain its six joints.
2. Evaluate calibrated FK with the intended TCP. Derive LOW by adding **0.020 m
   to base-frame Z**, preserving X/Y and orientation. This is an upward base-Z
   translation, not an offset along tool-local Z.
3. Teach HIGH as a separate joint configuration, then HOME separately.
4. Review/save the taught targets and validate actual robot/tool/board clearance
   before creating or running any motion sequence. A computed LOW pose is not
   proof of reachability, a clear path or a valid IK configuration.

The reader remains read-only; only the explicitly connected live hold-to-run UI
can command motion. M1 remains closed and M2 navigation is deferred while the
NFC board is unavailable.
