# E10: UR-mounted tool RS-485 no-load test

This experiment powers/brake-releases the UR using E7's stable-IDLE/RUNNING
sequence, keeps the arm stationary, and talks to the existing URCap forwarder
on TCP 54321 (as in E6). Robot address comes from `config/address.ini`.
The forwarder must already be installed/running, with flange/tool settings
matching the controller: **24 V, RS-485 115200 baud, 8N1**. No ROS dependency.

```powershell
# Help only: no connections or commands.
.\.venv\Scripts\python.exe experiments/e10_tool_rs485/tool_test.py

# Minimal read-only TCP PING before using the full test harness.
.\.venv\Scripts\python.exe experiments/e10_tool_rs485/ping_robot.py

# Real automated test: unloaded tool, no held floor or receiving structure.
.\.venv\Scripts\python.exe experiments/e10_tool_rs485/tool_test.py --run

# Explicitly configure tool I/O if needed (non-motion secondary URScript).
.\.venv\Scripts\python.exe experiments/e10_tool_rs485/tool_test.py --run --configure-tool

# Same bounded sequence using the USB adapter (close the serial monitor first).
# Robot still supplies tool power and is monitored over Dashboard/RTDE.
.\.venv\Scripts\python.exe experiments/e10_tool_rs485/tool_test.py --run --serial-port COM18 --keep-powered
```

Sequence: require Remote Control, NORMAL safety and no running robot program;
power on; wait for three IDLE reports; release brakes; wait for three RUNNING
reports; PING/VERSION; initial STATUS; STOP and three IDLE/zero-PWM confirmations;
read both motors' saved configuration; M1 tighten/stop/loosen/stop, then M2
tighten/stop/loosen/stop. Each motor run lasts **at most approximately 2 seconds**
(plus bounded command/cleanup latency), with STATUS polling about ten times per
second. `--seconds` accepts 0.5-3 seconds. One motor runs at a time. Any unexpected
stall/state, malformed reply or timeout fails the test without retrying motion.

Read-only RTDE joint samples before/after operations check that the arm remains
stationary within 0.01 rad of the post-startup reference. No moveJ/moveL, force
mode, RTDE control script, program load/play or protective-stop reset is sent.
With `--configure-tool`, a small secondary URScript sets `set_tool_voltage(24)`
and `set_tool_communication(True, 115200, 0, 1, 1.5, 3.5)`. This enables TCI
instead of tool analog inputs; it sends no motion statements. Output-only RTDE
readback verifies the tool voltage. The prior powered-state voltage is restored
before power-off when stop is confirmed; if stop is unconfirmed, immediate
power-off takes priority. Serial settings are not restored or saved to an
installation file. A successful voltage readback alone does not prove the
serial settings or tool communication succeeded; PING/VERSION must still pass.
This is a tool communication experiment, not a home/assembly motion test.

Cleanup attempts STOP and verifies both motors IDLE/PWM=0, then independently
sends Dashboard stop/power off and verifies POWER_OFF. A failed stop/power-off
verification is logged as FAIL with operator intervention required. Do not run
other UR/tool clients or use the tool's physical override buttons concurrently.
With `--keep-powered`, a PASS with verified final stop leaves the robot powered
for further tests. Failed/interrupted runs still attempt power-off. USB mode
uses the same protocol parser and motor sequence at 115200 8N1, without flow
control, manual DTR enabled and RTS disabled to match the working monitor;
it does not configure the UR serial interface. USB PING retries allow a
15-second boot window on a single open connection, with three-second replies.

## Firmware contract and limits

Reviewed source: [scaffolding_tool_controller](https://github.com/yck011522/scaffolding_tool_controller).
The current parser accepts `TIGHTEN M1/M2`, `LOOSEN M1/M2`, `STOP`, `STATUS`,
`PING`, `VERSION`, and `GET CONFIG M1/M2`. A STATUS reply is
`OK <M1 state> <M2 state> <current mA> <PWM percent>`, with states IDLE,
TIGHTENING, LOOSENING or STALLED. STOP clears stalled flags. The older RUN / STALL
examples at the end of the protocol document disagree with the current source
and are not used here. RS-485 is request/reply only; USB LOG/FAST/STATUSV are not
available over this link.

No firmware is edited/flashed and no SET/RESET/SAVE is sent: saved parameters
stay unchanged. The firmware warns its current-sensor range saturates around
750-800 mA although protocol limits allow up to 1500 mA and some factory loosen
setpoints are 900 mA. The test records actual saved configuration; it does not
retune it or establish current-limit accuracy. Review this before loaded tests.

No host-loss motor timeout was found in the reviewed firmware loop. Closing a
socket or terminating Python is not proof of motor stop. Runs are bounded by
the host; failure cleanup uses an independent Dashboard power-off path. Host/OS
failure and externally powered tools are not protected by that host cleanup.

A PASS establishes command acknowledgements, expected state/PWM observations,
idle checks and the power cycle. STATUS has no RPM/rotation field: it does not
independently prove shaft rotation, physical direction, grip, attachment or stall
accuracy. Deliberate stall tests belong in supervised E11 after E10 passes.

## Evidence

Every run writes flushed timestamped JSON records to `results/*.log`, including
raw TX/RX, initial INCOMPLETE status, motor samples, per-stage results, cleanup
and final PASS/FAIL/INTERRUPTED. Timestamps use Hong Kong time. An incomplete log
must not be treated as a pass.

### Live attempts, November 7, 2026

**USB no-load test PASSED at 16:05; robot-forwarded no-load test PASSED at
17:05 Hong Kong time.** The earlier failures below are retained as test history.

The operator added one **470 ohm resistor from B to the tool module's GND**
and reported restored standalone communication. This is the tested wiring;
no additional bias/termination changes were made by software. The precise
electrical failure mechanism and long-term noise margins were not established.
The earlier two-resistor suggestion was not the arrangement used for this PASS.

Minimal `ping_robot.py` returned `OK PONG`, followed by the
[passing robot-forwarded run](results/20261107_170450_085464_no_load.log).
Firmware v1.0.0 acknowledged all four two-second motor runs (M1 tighten/loosen,
M2 tighten/loosen), with expected running states/PWM and verified STOP after
each. No stalls were reported. Final status confirmed both IDLE/PWM=0; robot
left powered as requested. No deliberate arm trajectories or loaded tests ran.
The TCP client worked without changes after this hardware intervention.

[Passing USB run](results/20261107_160513_144911_usb_no_load.log): firmware
`v1.0.0`; M1 tighten, M1 loosen, M2 tighten, M2 loosen each completed a bounded
two-second run with 17 status samples (68 total), expected states and nonzero
PWM. No stalls reported; every operation and final cleanup confirmed both
motors IDLE/PWM=0. Running current samples ranged from 2 to 173 mA. Maximum
observed joint change was below 0.0001 rad. The robot was deliberately left
powered after PASS. This validates USB commands/state feedback, not physical
attachment, stall accuracy or the UR forwarder path.

Lessons from the USB investigation:

- A minimal pyserial PING succeeded without Dashboard operations, while the
  original E10 serial wrapper failed on the same adapter. Holding its driver
  configuration fixed resolved the failure. On Windows, changing pyserial
  timeouts invokes `_reconfigure_port()` and `SetCommState`; the old wrapper
  did this before writing and again while awaiting a fast response. The exact
  CH343 driver failure mechanism was not measured. New code uses a fixed
  nonblocking serial read configuration and implements deadlines in Python.
- Match the working monitor's manual control lines: DTR on, RTS off, no flow
  control. An opening settle and newline synchronize any partial old command
  before the handshake; only startup/sync bytes are discarded.
- Calibration-offset subtraction can produce a small negative idle current
  (observed `-1 mA`). Preserve signed current rather than rejecting that valid
  STATUS response. Stopped-state checks use IDLE and PWM=0.
- This USB fix does not explain the earlier TCP-forwarder failures, because
  the TCP path does not reconfigure pyserial. Reconnect the tool to the robot
  and test PING/STATUS before drawing conclusions about flange wiring or TCI.

- [First run](results/20261107_153135_081603_no_load.log): Remote Control and
  NORMAL safety checks passed, no UR program was running, and stable IDLE /
  brake-release / stable RUNNING succeeded. TCP 54321 accepted connections, but
  PING produced no reply throughout the boot window. No motor-start commands
  were sent. Dashboard cleanup verified POWER_OFF.
- A separate read-only RTDE check while powered off reported 0 V; that is not
  evidence that the powered-state configuration was wrong.
- [Explicit-I/O retry](results/20261107_153408_046895_no_load.log): the powered
  robot reported **24 V before setup**, so voltage alone did not explain the
  first failure. The non-motion I/O setup was sent and 24 V was read back,
  but PING again received no reply. No motor-start commands were sent. Prior
  voltage restoration and final POWER_OFF were confirmed. Outcome FAIL.
- [Longer-handshake retry](results/20261107_153907_886216_no_load.log): aborted
  immediately because Remote Control was off. No power, serial-configuration or
  motor commands were sent. The handshake now allows three seconds per reply
  to rule out a slow forwarder connection.
- [Powered longer-handshake retry](results/20261107_154012_228952_no_load.log):
  Remote Control restored; stable RUNNING and 24 V confirmed. Five PING attempts
  with three-second reply deadlines still received no bytes/replies. No motor
  commands were sent. Cleanup confirmed POWER_OFF. Outcome FAIL; increasing
  the handshake timeout did not resolve communication.

The operator confirms the tool is mounted and its OLED is powered only when
the robot is powered. OLED-off observations while the robot is off are expected.
During the powered retry the operator observed a startup message followed by the
normal screen. This supports successful application boot, but does not identify
the flashed firmware revision or prove RS-485 receive/transmit operation.

The operator subsequently confirmed freshly flashed firmware and demonstrated
PING, M1 tighten/loosen and STOP using a serial monitor through USB RS-485 COM18.
This establishes a working tool link with that monitor's settings and wiring.
The first automated USB attempt
([log](results/20261107_155826_542993_usb_no_load.log)) opened COM18 at 115200 8N1
but received no PING or STOP response. No motor-start commands were sent;
Dashboard fallback confirmed POWER_OFF. Monitor serial settings need comparison
before a further run. This automated USB run did not pass.

Two subsequent USB retries matched the reported monitor settings (115200, LF,
no parity, DTR on, RTS off):
[short reply timeout](results/20261107_160012_648150_usb_no_load.log) and
[persistent connection / longer boot wait](results/20261107_160103_216637_usb_no_load.log).
Both received no PING or STOP reply, sent no motor-start commands and confirmed
POWER_OFF through Dashboard fallback. These results do not contradict the
operator's earlier successful monitor test; the difference remains unresolved.
Compare a fresh monitor PING with unchanged wiring and power before another
automated motor run. The original pyserial defaults asserted both DTR and RTS;
the explicit setting change primarily corrects RTS to the reported off state.

Remaining physical/configuration checks: tool connected and OLED booted;
integrated firmware loaded rather than an older bench sketch; RS-485 A/B and
common reference wiring; URCap actually forwarding the flange UART at the
expected settings and no competing serial client. TCP acceptance only proves
the forwarder listener is reachable. No wiring/firmware fault has been proven.

Offline checks passed for syntax, split TCP reply assembly, compact STATUS
parsing and rejection of obsolete status framing. These do not replace HIL.

### Robot path reconnected, approximately 16:09-16:11 Hong Kong time

The operator reconnected the tool RS-485 to the robot after the USB PASS.
The new minimal `ping_robot.py` connected to TCP 54321 but timed out awaiting
PING. A separate persistent plain socket also received no response to a newline,
PING or STATUS. No motor commands or power changes were sent.

Read-only Dashboard checks showed RUNNING, NORMAL safety and no running program.
Reading tool-communication subpackage 11 on read-only secondary port 30012
confirmed **enabled=True, baud=115200, parity=0, stop_bits=1, RX idle=1.5,
TX idle=3.5**. Thus serial settings are now verified independently of the
earlier setup-script/voltage checks. The remaining unresolved area is the
robot-side wiring/forwarding path; a wiring fault has not been established.
Robot power was left unchanged. Packet layout follows the
[official primary/secondary guide](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/primary-secondary-guide.html).

### Further software investigation, approximately 16:12-16:15 Hong Kong time

The operator checked and confirmed wiring. Investigation continues on the
software/forwarder side rather than asking for repeated wiring changes.
Plain TCP PING tests using LF, CRLF and 50 ms inter-byte pacing, on one
connection with TCP_NODELAY and a two-second opening settle, all timed out.
See [framing probe results](results/tcp_framing_probe.json). These are read-only
tests: no motor/power commands were sent.

The official [forwarder daemon source](https://github.com/UniversalRobots/Universal_Robots_ToolComm_Forwarder_URCap/blob/master/src/main/resources/com/fzi/rs485/impl/daemon/daemon-rs485.py)
runs `socat tcp-l:54321,reuseaddr,fork file:/dev/ttyTool,nonblock,raw,waitlock=/var/run/tty`.
A client can therefore connect before it acquires the serial-device lock;
TCP acceptance does not establish UART ownership. A waiting lock or competing
reader is a hypothesis, not a confirmed diagnosis. Key-only SSH access to read
robot processes was denied; no passwords were tried and no robot files changed.
Next isolate daemon state by stopping/starting the RS485 URCap daemon while
keeping robot power unchanged, then rerun the same minimal PING. If unsuccessful,
inspect actual daemon processes/logs and UART configuration with authorized
robot-side access. No firmware change is justified by these observations.

### E11: supervised attachment/stall test

Create a separate operator-driven test with explicit prompts and bounded motor
actions: position/support a floor plate, tighten M1 until STALLED, confirm grip;
position the receiving structure through the attached floor, tighten M2 until
STALLED, confirm attachment; support parts and loosen M2 in bounded steps until
the operator confirms disengagement, then support the floor and loosen M1.
STOP follows each operation and faults/cancellation; never retry a jam
automatically. The arm stays stationary. Part removal and power-off behavior
need operator confirmation so software does not unexpectedly release or leave
a partially attached object. The no-load gate has now passed on USB and the
robot-forwarded path. See `../e11_tool_interactive/README.md` for the prepared
interactive test. The operator subsequently completed E11 successfully; its
README and log record both tightening stalls, attachment/release confirmations
and final verified stop. This is one supervised trial, not endurance acceptance.

Source fingerprints reviewed: protocol.cpp `45186138b220e9b0eee9e7203de2efd46c46010f`;
transport.cpp `a300701a8d3fe29a1bacefa943844290645070d7`.
UR status/power commands follow the [official Dashboard interface](https://www.universal-robots.com/manuals/EN/HTML/SW5_25_1/Content/prod-dashboard/Dashboard_table.htm).
Tool I/O follows the [official TCI function](https://www.universal-robots.com/manuals/EN/HTML/SW5_26/Content/prod-scriptmanual/all_scripts/set_tool_communication.htm)
and [RTDE output fields](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html).
