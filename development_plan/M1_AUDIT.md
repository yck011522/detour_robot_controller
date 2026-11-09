# M1 closeout audit

Date: 2026-11-09. Scope: PC communication, real HID, four basic video/status
views and admin diagnostics, with one real car and other cars Not Present.
This audit performed no live motion, hardware disconnect or firmware change.

## Verdict

**M1 accepted and closed on 2026-11-09 for the current one-car setup.**
After the audit, the operator confirmed both remaining physical checks passed:
gamepad/modem runtime unplug/replug, and held-key focus changes. The operator
then explicitly requested M1 closeout. These are operator-reported hardware
results; no new log filename was supplied for those final checks. They are not
being presented as independently replayed or newly automated evidence.

No additional UI redesign, UR integration, graph/map work or four-car build is
needed to close M1. Firmware recovery changes can proceed independently in the
other agent. Keep the stopped-but-receiving incident as a tracked firmware
compatibility limitation, not an unexplained PC transport failure or a fixed bug.

## Acceptance matrix

| M1 requirement | Evidence | Status |
|---|---|---|
| Fixed COM, frozen 7/21-byte payloads, strict parsing | Direct COM11 probe; raw TX/RX logs; known-vector, offsets, malformed-input and sequence tests | Verified |
| Real index-0 HID and configured Car 1 buttons | Generic USB Joystick with 12 buttons; logs contain forward, left, right and combinations from configured buttons | Verified for available Car 1 controls |
| Player intent reaches moving car | `20261109_155516_756129.log`: normal-mode echoed commands `0x03`, `0x0B`, `0x13` accompanied by measured wheel movement | Verified; not a calibrated physical steering/latency measurement |
| Admin AUTO/MANUAL, reverse and pivots | Same log and `20261109_164052_632115.log`: admin commands plus nonzero measured wheels, including MANUAL reverse and pivots | Verified within one-car bench scope |
| Four-screen UI, independent offline cameras | 35/45-second stop-only checks, screenshot inspection, one active and three offline camera views | Verified for current setup; four-active-stream integration load remains expanded coverage |
| Admin colors/hide-show and quit confirmation | 4-window smoke `20261109_163137_213231.log`; operator confirms admin panel and Enter/Escape quit work; overlay tests | Verified; final artwork is M6 |
| No separate drive-enable switch | Removed from code/config/UI; mode/input and existing eligibility decide command; STOPPED regression test | Implemented; current build stop-only smoke passed |
| 50 Hz independent control | Long session `20261109_161105_479146.log`: final mean interval ~20.009 ms, p95 ~20.370 ms, 8 skipped ticks over ~11 minutes; camera corruption sampled zero | Verified as soft-timed PC generation, not guaranteed BLE delivery |
| Car off/on and retained presence | Operator confirmation; `20261109_160617_806806.log`: 14.526/8.141 s feedback gaps, only stopped AUTO TX after stale threshold, fresh echoes restored | Verified; future zone-token/localization reconciliation belongs to M3 |
| Fatal missing startup modem/gamepad | Two new hardware-free main-path tests: failure result, error detail, no continued application/drive startup | Verified in fault-injected tests; not a physical cable-removal test |
| Runtime fixed-port modem recovery | Simulated serial failure test reconnects only to configured port; source-reviewed bounded worker | Passed: operator confirmed physical unplug/replug at closeout |
| Runtime HID recovery / immediate held-input resume | Tested state rules and E2-derived instance-ID handling; no recorded GAMEPAD_LOST in inspected operator logs | Passed: operator confirmed physical unplug/replug at closeout |
| Focus-loss / held-key transitions | Stale keyboard intent timeout and focused-window snapshots implemented; synthetic window key routing passed | Passed: operator confirmed focus/held-key check at closeout |
| Shutdown stop observation | Inspected completed sessions record stop echoes and wheel speeds <=5 mm/s with Car 1 confirmed | Verified as reported stop, not independent physical-rest proof |
| Synthetic support | Small fake serial/telemetry/Car fixtures in automated tests; runtime other cars explicitly Not Present | No interactive runtime synthetic-car adapter; not needed for current one-car scope. Add tag-event injection with M2 if needed |
| UR/tool exclusion | No RTDE imports, UR sockets, homing, power or tool commands in M1 app | Scope respected; production startup/HOME exit deferred |

Re-ran **16 hardware-free tests** during this audit; all passed. This includes
two added fatal-startup fault-injection checks. `git diff --check` passed.
No new hardware experiment was performed for the audit.

## Final operator closeout checks ? passed

The operator confirmed both checks below passed and requested closeout on
2026-11-09. The steps are retained for regression testing after future changes.

1. **Device disconnects:** separately unplug/replug the gamepad and modem USB.
   Observe stopped commands/device status, no application exit after runtime
   loss, reconnect to the same configured modem port, and resumption from current
   held input once fresh. This is distinct from fatal absence at initial startup.
2. **Keyboard focus:** in admin, hold an arrow/Left Ctrl while changing focus
   between the four windows and another application, then release it. Verify no
   stuck motion/direct-control indication, Space still changes mode, and pending
   Q confirmation suppresses driving until Enter/Escape. Keep the expected
   immediate-held-input policy when normal control becomes eligible again.

M1 is now accepted based on the recorded evidence and this operator confirmation.
For future regression runs, record the result and corresponding log filename. Do not require a fifth screen, a full track, all four cars
or robot movement for this closeout.

## Known issue and firmware-agent handoff

See `../docs/M1_UNRESPONSIVE_CAR_REVIEW.md` and `E16_FIRMWARE_CHANGES.md`.
The strongest explanation for the earlier lockout is E15's post-NFC recovery
forward/valid-line interlock blocking MANUAL too. In the affected log, 248
telemetry packets echoed forward commands while reporting STOPPED and mask zero.
The internal latch was not captured, so this remains a source-backed hypothesis.
PC code does not withhold an otherwise eligible operator command merely because
state 15 is reported; it now shows a warning. No autonomous reset/forward pulse.

Give the other firmware agent both documents and the frozen interface reference:
`../docs/protocol_reference/car_communication_protocol_snapshot.md`, interpreted
alongside `INTERFACES.md`. Preserve these integration constraints:

- Exactly 7-byte World State and 21-byte Robot State, existing field offsets,
  movement bits, state-code numbers, four-byte UID and USB framing.
- Compatible BLE service/characteristic UUIDs and discovery identity. Do not
  change the discovery version simply because the experiment is named E16;
  the existing E13 modem expects the current advertised identity format/version.
- PC-owned tag semantics: no firmware map graph, whitelist or tag-order recovery.
- Existing watchdog, battery/line/NFC protections, with any changed post-recovery
  MANUAL behavior explicitly reviewed, tested and documented by that agent.
- Echo remains receipt, not execution. If firmware adds diagnostic text, keep
  existing binary telemetry and modem compatibility intact.

After flashing an approved new build, rerun a minimal stop-only probe, commanded
state/echo checks and supervised AUTO/MANUAL/recovery tests. The audit has not
verified any future E16 build. Its flashed source identity should be recorded in
the next test log rather than assuming current E15 evidence transfers unchanged.
