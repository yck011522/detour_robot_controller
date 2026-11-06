# E7: automatic robot start and stop

This script uses the Universal Robots Dashboard Server on TCP port `29999`:

1. Confirms Remote Control mode.
2. Powers on the robot if it is currently `POWER_OFF`.
3. Waits up to 15 seconds for three consecutive `IDLE` reports before sending
   `brake release`, then waits for three consecutive `RUNNING` reports.
4. Waits five seconds, sends `stop`, then sends `power off`.
5. Waits for the robot to report `POWER_OFF`.

If the robot is already in `IDLE`, the script verifies that it remains in
`IDLE` before sending `brake release`. If it is already `RUNNING`, it verifies
that state before starting the timer.

For a full startup test, boot the controller with the robot arm in `POWER_OFF`,
and ensure Remote Control mode is enabled. Run from the project root:

```powershell
.\.venv\Scripts\python.exe experiments/e7_arm_auto_start_stop/auto_start_stop.py
```

This commands robot power and brake state. Only run it when the robot and
attached tool are clear and safe for brake release and shutdown.

The Dashboard Server reports `Brake releasing` when the brake-release command
is accepted. `IDLE` is not the ready state: it means the robot is powered on
with brakes engaged. The script now waits for three consecutive `RUNNING`
reports before starting the five-second timer. Universal Robots documents
`RUNNING` as powered on with brakes released and ready for a program; see the
[UR robot state helper documentation](https://docs.universal-robots.com/Universal_Robots_ROS2_Documentation/doc/ur_robot_driver/ur_robot_driver/doc/robot_state_helper.html).

If `IDLE` is not reached within 15 seconds, the script exits without issuing
`brake release`, `stop`, or `power off`. If the Dashboard Server rejects a
command, the script reports its response and exits. In the 2026-11-06 fresh
reboot test, the earlier script sent `brake release` at `POWER_ON`; the robot
subsequently entered `BOOTING` and `IDLE`. This change waits for `IDLE` before
requesting brake release. The controller's rejection message alone does not
establish whether the cause was mounting configuration or startup timing.

## Live test result on 2026-11-02

Three earlier full power-up, brake-release, five-second wait, stop, and
power-off cycles completed, but the readiness check used `IDLE`. The user
observed that the pendant still indicated brake release / not operational.
Those runs therefore did not verify readiness. The corrected check now uses
three consecutive `RUNNING` reports; repeat live results are recorded in the
[test summary](results/auto_start_stop_2026-11-02_summary.log).

The corrected script was then run for three full live cycles. In every cycle,
the robot reported `IDLE` for several polls after the `Brake releasing`
acknowledgement before transitioning to `RUNNING`. The script waited for three
consecutive `RUNNING` reports before beginning the five-second wait. All three
cycles stopped and powered off successfully; a final read-only query confirmed
`POWER_OFF` and Remote Control mode.

## Fresh-reboot test on 2026-11-06

The user reported a successful run after a fresh reboot: the script waited
through `POWER_ON` and `BOOTING` for three `IDLE` reports before sending
`brake release`, then waited for three `RUNNING` reports before the
five-second wait and power-off. The later code cleanup has been checked offline,
but has not been separately rerun on the robot.
