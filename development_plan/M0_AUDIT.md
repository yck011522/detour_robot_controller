# M0 audit and proposed M1 test scope

Audit date: 2026-11-09 (Hong Kong). No hardware connections, firmware edits,
flashes, robot power operations or live motion were performed in this audit.

## Verdict

**Subsequent authorization, 2026-11-09:** the user explicitly requested M1
implementation after resolving the three policy choices. The sign-off caveat
below records the state at initial audit; it no longer blocks M1 work. Initial
implementation and stop-only evidence are in `../docs/M1_TEST_GUIDE.md`. M1
acceptance is separate and still needs operator driving/recovery checks.

M0's technical review and architecture deliverable are now recorded. The three
immediate policy decisions were resolved by the user on 2026-11-09, as recorded
below. M1 is technically ready to begin; explicit approval to implement the
bounded M1 scope has not yet been recorded. Policy answers alone are not a claim
that M1 implementation or acceptance has happened.
M1 has not been implemented or accepted. E16 and final Rhino/UI design need not
block M1 communication diagnostics; they do block full-map navigation acceptance.

## Evidence and compatibility matrix

The canonical protocol was fetched and preserved byte-for-byte in
`../docs/protocol_reference/car_communication_protocol_snapshot.md`, Git blob
`9ed63a1774545cc294a62bb70c5ddf84323eb64d`. It matches the local protocol file
after normal text decoding. Source audit used the clean local
`C:/Users/scmuser/Documents/line_follower_control` checkout at commit
`162b2aa84fa18f81f871e16ef1dfef5da900dea1`. This identifies reviewed source,
not the firmware actually flashed onto any particular device.

| Area | Evidence inspected | Finding / M1 implication |
|---|---|---|
| World State | E13 shared `ble14_protocol.h`, E15 command handler, canonical document | `<HB4B`, 7 bytes; USB `W` + 14 hex digits + LF; 50 Hz PC target. Offline known-vector check yields `W34120201030500` for the published example. |
| Robot State | Shared packed struct/static assertions, E15 telemetry publisher | `<BHHB4sHBBBhhH`, 21 bytes; USB 42 hex digits + LF. Separate line-sensor, controller and follower bytes at offsets 12/13/14. Offline size checks passed. |
| Modem | `src/e13/modem/main.cpp`, used by E15 | Latest shared snapshot, 1000 ms mailbox expiry, up to eight outstanding sequence credits per peer, telemetry-driven credit release, independent peer progress and bounded USB feedback. No direct BLE-connected flag in PC telemetry. |
| E15 AUTO commands | `selectLineFollowerStateFromTurnBits()`, `applyWorldMovementCommand()` | No turn bits = FOLLOW_LINE; left/right choose edges immediately; both = BRANCHING_CENTER. Forward clear stops AUTO; keep auto bit set (`0x01`). |
| E15 MANUAL | `setManualWheelTargets()` | Direct wheel commands, reverse/pivot and right precedence for both turn bits; forward+backward stops. PC sanitizes incoherent physical input; preserve firmware. |
| Protections / NFC | E15 `main.cpp`, car README | 500 ms World State-loss stop, line-loss/battery/NFC recovery; fixed eight-tag cycle remains. E16 brief is required for arbitrary-map navigation, not basic M1 transport. |
| Input | E2 source and prior working experiment | Single Pygame HID at index 0, >=12 buttons, instance-ID removal and retry; use configured mappings. E2's 100 Hz becomes 50 Hz for M1. No new live validation in this audit. |
| Four videos | E9 source and README | Four processes, H.264, RTSP/TCP, PyAV CPU decode, YUV texture upload and screen-attached renderer. Historical all-H.264 smoke check is recorded; 30 fps is conservative, not guaranteed 60 fps. Raw historical benchmarks were removed, so README evidence is not a fresh benchmark. |
| Tool | E10/E11 READMEs and retained experiment results | Robot-forwarded no-load PASS and one supervised loaded attachment/release PASS; tested B-to-GND 470 ohm arrangement. No production force/depth/trajectory or endurance acceptance. |
| Configuration | `config/address.ini` | Cars 1-4 use displays 2/3/0/1 and buttons 0-11; modem COM11 is fixed by policy. User confirmed configured Space toggle / Escape cancel; plans reconciled. Spare camera 6 and UR both list 192.168.0.10; do not activate that spare concurrently without resolving its address. |

### Published document versus implementation

The frozen document mixes Test 14/E13 historical and intended behavior with
E15 additions. These are documented discrepancies, not permission to change
firmware or packets:

- It says both AUTO turn indicators select right; reviewed E15 selects
  BRANCHING_CENTER. The user already chose to preserve actual firmware and
  sanitize contradictory physical input on the PC. MANUAL right precedence
  remains as documented. No tie-break firmware change is needed.
- Its tag visibility/branch classification and planned local topology logic
  are historical intentions, not the current game design. PC distinct-UID
  attributes now own navigation; E15 itself still rejects unknown/out-of-order
  tags. E16 removes that restriction, without adding a firmware map.
- Its topology field describes a predefined-map ID. The approved PC delta
  assigns zone-availability/admin bits without changing location or size;
  reviewed E15 does not enforce topology. Echo is receipt only.
- Configuration commands are explicitly unimplemented. Do not send a proposed
  `C` command or expect an acknowledgement/channel absent from tested firmware.
- State labels are not proof that a car can move: NFC recovery/battery/line-loss
  may suppress motion even after a World State was received. UI should display
  commanded state and reported state separately.

## Agreed immediate M1 decisions (2026-11-09)

1. **Admin shortcut:** keep configured Space toggle / Escape cancel. Read
   `[admin_keyboard_shotcut]`; UI labels reflect effective bindings. Escape is
   not a second toggle or the E9-style immediate exit.
2. **Driving reenable:** resume immediately from currently held inputs after
   leaving admin or recovering the gamepad/car connection. No release or fresh
   press is required. Recompute commands from fresh current input/telemetry and
   current selection/mode; do not replay an old cached movement byte. Existing
   stop/fault, communication freshness and zone/localization eligibility rules
   still apply. This decision does not clear reservations or bypass firmware
   protections.
3. **Missing startup device:** show the error and quit when modem or gamepad is
   unavailable. For modem failures report configured and available COM ports;
   for gamepad failures report availability/mapping details. No diagnostics-only
   stay-open exception was approved. Runtime loss/reconnect remains distinct
   from fatal initial startup failure.

Set named configurable telemetry/echo/video-age thresholds before M1 live tests.
Proposed initial diagnostic values: suppress driving after 500 ms without valid
ordered telemetry or observed echo progress; label video stale after 1 s without
a decoded frame; require fresh observations plus the selected reenable rule.
These are tuning proposals, not measured end-to-end stopping guarantees. Reset/
wrap ambiguity must not manufacture echo progress or packet-loss claims. Confirm
thresholds in the M1 test plan; do not change firmware timeouts.

## Later logic inconsistencies / boundaries

These need resolution before their milestone, rather than blocking M1:

- **AUTO versus MANUAL token rules (M2/M3):** normal zone splits require a
  token even in admin AUTO, but existing MANUAL controls bypass AUTO steering.
  Define how manual recovery is authorized relative to robot zone ownership.
  Never describe unrestricted MANUAL steering as token enforcement.
- **Branch commitment (M2/M3):** retrying a denied request is agreed. Decide
  when switching safeexit to the zone branch is too late after forward travel
  has begun, and test actual tag spacing/latency. A car stopped in approach can
  wait; a moving car may already have committed to the bypass.
- **Conflicting attributes (M2):** final JSON must define validation for split
  plus adminsplit, merge plus split, cross-zone transitions and missing safeexit.
  The current rule rejects ambiguous declarations rather than inventing priority.
- **Evacuation/admin (M3/M4):** approach safeexit and inside forward+merge rules
  are agreed. Still settle whether scheduling/timers pause in admin, and the
  response to inside-without-token or a different-zone request while holding one.
- **Unknown tags:** treating actually observed unknown UIDs as all-none and
  releasing tokens is explicitly accepted. Keep this policy; do not confuse it
  with zero UID before first read, stale telemetry or telemetry loss. A token
  release is not an independent physical-clearance measurement.
- **Charging/reappearance (M1 bounded reconnect; M3 full reconciliation):** no
  wire session token exists. Define reboot/sequence ambiguity, fresh localization
  and retained reservations before robot/car coordination. Do not clear tokens
  on a temporary disconnect or infer Not Present from silence.
- **Tool workflow (M5):** E11 establishes distinct M1 gripper and M2 installation
  actions. The older production pickup/place text still describes one generic
  tightening/unscrewing sequence. Specify both motors, attachment transitions,
  force/depth criteria and fault handling before autonomous board transfer.
- **Exit/startup scope:** production requires inventory, assisted homing and
  verified HOME exit, but M1 excludes robot actuation. Declare a diagnostic-only
  profile that never starts robot/tool control, or full production startup would
  block this early milestone. With no robot ownership, quit stops car intent,
  keeps sending stop for a bounded observed interval when possible, then closes;
  link failure is logged as unconfirmed stop, never claimed confirmed.

## Proposed minimal M1 UI

Four screen-specific test views, with the existing camera/display mapping:

- Video or a clear connecting/unavailable/stale placeholder; UI updates even
  when no camera frame arrives. No map or final visitor artwork required yet.
- Car number and independent **car feedback**, **command echo** and **video**
  health/age indicators. Never infer car reachability from camera playback, or
  BLE connection from the configured modem port merely opening.
- Compact battery, UID/age, reported controller/follower state, raw input and
  effective AUTO/MANUAL command. A diagnostic detail panel may show sequences,
  loss/discontinuities and loop timing without cluttering all player views.
- A prominent **Game Paused / Admin Control** overlay on all four screens,
  showing selected car/mode. Visitor driving input is suppressed; video and
  telemetry continue. Admin driving is explicitly indicated on the selected view.
- A basic admin panel: car selection, Present/Not Present, device diagnostics
  and quit. Show modem/gamepad availability and reason when driving is disabled.
  Presence is an operator assertion, not a synonym for reachable.

E9 is a reusable rendering path, not a complete UI implementation. Its overlays
freeze with video, one stream failure stops all windows, and Escape/Q exits the
group. M1 must separate UI refresh from decoded-frame availability, isolate
camera failure, and relay keyboard/window events to the central controller.
Do not claim global keyboard capture outside focused application windows;
verify focus changes between all four displays and lost key-up handling.
Final layout, minimap/HUD artwork and complete recovery UI stay in M6.

## Proposed architecture and implementation sequence

One central game/controller process owns authoritative state, Pygame joystick
polling, admin arbitration and World State generation/submission together at
50 Hz. Keep SDL input pumping on that process's main thread. Following the M1
operator refinement, its SDL event-owner window is hidden: all visible admin
controls live on the four player overlays, with no fifth visible panel/window.

A serial worker continuously drains/parses telemetry and writes generated
snapshots with finite I/O waits. Outbound state is latest-only and carries the
controller's generation time: the worker must never keep replaying stale driving
intent if the controller stalls. Preserve per-car distinct UID/sequence events
in order; do not coalesce away an intermediate tag. Bound queues and expose
overflow/discontinuity rather than silently losing a zone transition.

Four E9-derived display processes own their SDL windows/renderers; each decoder
thread retains only its latest frame. Bounded replaceable UI snapshots go from
the controller to displays; keyboard events and video/frame-age/error reports
return to the controller. Display failure never blocks the control tick.
Initially use plain functions and small records; avoid a generic adapter or
simulation framework before a minimal serial/input/status path works.

Implement in small reviewable steps:

1. Direct fixed-COM protocol/telemetry diagnostic with stop-only outputs; then
   add the tested E2 input loop and selected-car supervised bench control.
2. Add central state, admin/presence arbitration and the agreed reconnect gates;
   keep robot/tool unowned and inactive. Use E15's supported bench/tag setup.
3. Attach four independently refreshed video/status views and verify cross-window
   admin controls. Use explicit synthetic/no-device car records where needed.
4. Validate the combined loop under video load and produce M1 evidence before M2.

## M1 acceptance checklist

- Validate 7/21-byte layouts, the known World State vector, strict line framing,
  invalid/debug text rejection, car IDs and duplicate/stale/reset/wrap handling.
- Verify actual gamepad button mappings, AUTO stopped/forward/turn and selected
  admin MANUAL/reverse on the available car in an authorized supervised setup.
- Toggle admin from each application screen: all overlays update independently
  of camera delivery, visitor input is suppressed, no stale command survives a
  selection/mode/focus transition. Test missing key-up/focus loss.
- Show independent feedback/echo/video health with named thresholds. Unplugging
  a camera does not mark its car absent or stop unrelated screens/control.
- Measure 50 Hz generation/submission timing (distribution, missed deadlines,
  no catch-up bursts), observed telemetry rates and echo progress under four
  video streams. Do not promise 50 Hz delivery or locked 60 fps.
- Exercise configured-COM reconnect, gamepad removal, car off/on, selected
  reenable policy, startup-error behavior and bounded diagnostic shutdown.
- One car is enough for initial physical validation; label synthetic/Not Present
  peers and confirm which car controllers actually exist. Four video streams
  do not establish four functioning BLE cars.
- Record exact runtime/build/config, timestamped transition/command logs,
  hardware versus synthetic coverage and any unresolved limitations. No robot
  movement, arbitrary-map navigation or M3 reservations claimed accepted by M1.

No further standalone hardware experiment is mandatory before starting this
bounded M1. The combined input/modem/video/recovery test is the milestone's
acceptance work, not something already proven by E2 or E9 alone.
