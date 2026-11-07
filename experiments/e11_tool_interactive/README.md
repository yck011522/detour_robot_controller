# E11: supervised attachment and release

Prepared after E10 passed its no-load sequence over the robot RS-485 forwarder.
**Supervised hardware test PASSED on November 7, 2026.** Keep the E10-tested wiring, including the
operator-installed 470 ohm resistor from B to tool-module GND.

```powershell
# Help only; no hardware access.
.\.venv\Scripts\python.exe experiments/e11_tool_interactive/tool_interactive.py

# Run from a terminal where you can answer prompts and press Ctrl+C.
.\.venv\Scripts\python.exe experiments/e11_tool_interactive/tool_interactive.py --run
```

The robot must already be RUNNING, in Remote Control, with NORMAL safety and
no running program. This script performs no power/brake operations or arm
trajectories. It reuses E10's tested TCP framing, status parsing, stop checks and
timestamped logs. Results go to this folder's `results/` directory.

1. Support/position the floor plate; type RUN to tighten M1 until reported stall,
   with a five-second limit. STOP follows; confirm physical attachment.
2. Support/position the receiving structure; repeat for M2 and confirm attachment.
3. Support the parts; loosen M2 in individually prompted one-second steps.
   Type DONE and confirm only when physically disengaged.
4. Repeat release steps for M1. Final STOP verifies both motors IDLE/PWM=0.

No input is requested while a motor is running. Ctrl+C attempts STOP; faults,
unexpected states, missing stalls or loosening stalls abort without automatic
motion retries. STOP clears the firmware stall flags, so the log records the
stall before sending STOP. Stall feedback alone is not proof of secure grip;
the operator must explicitly confirm each attachment. No persisted tuning changes
are sent. Current configuration is logged, including the known E10 current-sensor
range caveat; this test does not validate current-limit accuracy.

Support all parts throughout, including after a fault. Robot power remains
unchanged on success or abort; there is no automatic homing, reversal, recovery
or power-off that could release a partially attached part. If STOP cannot be
confirmed, the program explicitly requests immediate operator power removal.
The firmware has no host-loss motor watchdog: a PC crash or killed process can
leave a motor running, so independent tool power-off must be available.

Offline flow checks are not loaded-hardware acceptance. PASS requires both
reported tightening stalls, operator confirmations, the release sequence and
verified final stop. A partial/INCOMPLETE log is not a pass.

## Observed result

[Completed run](results/20261107_170912_682786_supervised.log), 17:09:12-17:10:41
Hong Kong time (about 89 seconds):

- M1 tightening reported STALLED; operator confirmed attachment.
- M2 tightening reported STALLED; operator confirmed attachment.
- M2 loosened in one bounded step without stall; operator confirmed disengagement.
- M1 loosened in one bounded step without stall; operator confirmed disengagement.
- Final STOP was acknowledged, followed by three STATUS replies showing both
  motors IDLE and PWM=0. Outcome PASS; power unchanged.

This verifies the supervised sequence for the tested parts/setup. Attachment and
disengagement were operator-confirmed; the log does not independently measure
grip force, fastening torque, long-term communication reliability or robot-motion
performance. No arm trajectory was commanded by this script.
