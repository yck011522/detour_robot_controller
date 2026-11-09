# Review: car receives commands but stays STOPPED

Reviewed 2026-11-09 after the operator reported needing a physical reboot.
This is a log/source investigation, not a confirmed reproduction of the internal
firmware state. No firmware was modified/flashed and no recovery motion was sent.

## What the logs establish

- In `logs/m1/20261109_163357_047267.log` and
  `20261109_163913_908996.log`, Car 1 continued sending ordered telemetry and
  progressing World State echoes, but every decoded controller state was
  **STOPPED (15)** and reported wheel speeds remained zero.
- In the first run, **248 telemetry packets echoed locally recorded forward
  commands while still reporting STOPPED**. All 248 reported line mask **0**.
  One example is 16:35:57.103, echo 5965, command `0x03` (AUTO + forward).
  Thus the PC was not merely holding the enable switch off, and BLE reception
  was working during these observations. Receipt alone is not motor execution.
- During the stopped runs, the mask was usually zero; brief nonzero masks in
  the longer run did not overlap those echoed forward-command observations.
  Telemetry mask zero means no reported line, including an invalid sensor read;
  it does not distinguish the two.
- The next run, `20261109_164052_632115.log`, after the reported reboot, showed
  initial STOPPED followed by AUTO/MANUAL and measured wheel movement. Its
  line masks included valid patterns such as `0x70`.

## Strongest explanation in reviewed E15 source

`src/e15/car/main.cpp` in the sibling firmware repository:

1. `beginNfcRecovery()` sets `requireFreshForward=true`, stops targets and
   retains that interlock through recovery.
2. Successful `serviceNfcRecovery()` returns to STOPPED unless battery-blocked,
   but does not clear `requireFreshForward`.
3. `applyWorldMovementCommand()` rejects **both AUTO and MANUAL** while the
   interlock is set, unless the request includes forward without backward and
   the current line read is valid, neither all-line nor no-line, and not LINE_LOST.
4. A reboot clears the in-memory interlock. Normal MANUAL repositioning can then
   work again even if the line was lost, subject to the other protections.

This can look like a completely unresponsive car despite healthy BLE: a car
off the line after NFC recovery cannot use MANUAL/reverse to reposition until
it first satisfies the forward-plus-line condition. NFC recovery can be
periodic or caused by a reader error/invalid E15 tag transition. The logs do
not expose the private interlock or prove which trigger occurred, and the exact
flashed build was not read. No claim is made that a firmware crash was proven.

## Changes and future action

- Removed the PC enable/disable-driving switch/config field. Normal mode uses
  current joystick intent; admin mode uses the selected car's current keyboard
  intent. Presence, stale feedback/echo, device loss, active firmware protection
  and pending quit still suppress commands. `--stop-only` remains an explicit
  diagnostic override, not a UI arming control.
- Show STOPPED/no-line as a **possible post-NFC interlock**, rather than making
  the operator infer that a healthy echo means the motor accepted the command.
- Regression test verifies that PC STOPPED handling does not withhold forward
  or MANUAL intent merely because state 15 is reported. No automatic forward
  pulse, blind reset or protection bypass is introduced.
- Before E16 adoption, separately review allowing deliberate MANUAL recovery
  after NFC initialization has completed even without a line. Preserve battery,
  active-recovery, motor and communication protections. This is an additional
  firmware decision, not part of the already documented whitelist-removal delta.
  It requires explicit firmware implementation/test authorization.
- On another occurrence, preserve logs and inspect reported state, mask and
  echoed intent before reboot if practical. A valid-line forward request may
  release the existing interlock, but software must not issue it autonomously.
