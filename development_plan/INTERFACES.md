# PC–BLE modem–car interfaces — frozen protocol and approved topology meaning

**Canonical source (authoritative, unchanged transport):**
https://github.com/yck011522/line_follower_control/blob/main/docs/remote_control_payload.md

**Current tested car baseline:**
https://github.com/yck011522/line_follower_control/tree/main/src/e15/car

**IMPORTANT:** This is a companion integration reference to the existing repository document, **not a replacement for its complete verbatim contents**. The full linked `remote_control_payload.md` governs exact modem behavior, framing, timing, sequence handling, BLE UUIDs and all field definitions not explicitly overridden below. At M0, the coding agent should copy that canonical file verbatim into the new controller repository (for example, `vendor/remote_control_payload.md`) and preserve it, then retain this approved delta alongside it. If the copied file differs from E15, report the difference and get user approval before assuming any changes. Do not edit or modify either firmware or protocol under this project without specific permission.

## Approved change: interpretation of `topology_id` only
The existing World State **uint8** field at BLE offset **2** is reinterpreted as an effective **zone-availability bit mask**. This is a **semantic change only**; packet size, framing, byte order and location remain unchanged.

| Topology bit | Meaning | 1 | 0 |
|---|---|---|---|
| 0 | Zone A | Route into A available | Route into A unavailable |
| 1 | Zone B | Route into B available | Route into B unavailable |
| 2 | Zone C | Route into C available | Route into C unavailable |
| 3 | Zone D | Route into D available | Route into D unavailable |
| 4 | Administrator mode | Selected | Not selected |
| 5,6,7 | Reserved | Must be zero until agreed | Reserved |

**Zone availability is NOT synonymous with board presence.** It is an effective permission computed by the PC from whether a floorboard exists and whether the robot has closed the zone. A car's own reservation for an already-available zone does not, by itself, close that zone. The PC tracks board occupancy, reservations and pending robot claims separately. PC admin behavior is agreed in SYSTEM_SPEC.md section 12. Firmware navigation behavior for admin bit 4 remains deferred; do not invent it.

At the time of drafting, published E15 receives World State but does not yet apply `topology_id` to branch navigation. Making car firmware enforce topology will require a *separately approved firmware iteration*. PC functionality may prepare/transmit the byte without claiming that enforcement works already.

## Existing, unchanged transport path

`Windows PC → USB serial (COM) → BLE modem → BLE car(s)`.

This **does not use ESP-NOW**. Older tests in the repository do not describe the production modem or E15 transport.

- One **shared** PC-generated World State is transmitted via the modem to up to four cars. **Agreed PC control cadence: 50 Hz (20 ms), with USB HID gamepad polling and World State generation/submission in the same tick.** Display redraw is independently paced at 30/60 fps and must not drive or block control timing. The modem handles BLE scheduling and can supersede older snapshots; a 50 Hz PC submission is not a promise of delivery at exactly 50 Hz to every car.
- Cars produce individual Robot State notifications at nominal **20 Hz**, relayed over USB serial to PC.
- Multi-byte fields are **little-endian**.
- The modem's existing USB and BLE behavior should be treated as frozen, including bounded queues, coalescing, credits, expiry and transport loss effects. Only the original source document and existing firmware govern these details.

### World State: exact existing wire layout

| BLE offset | Name | Type / bytes | Semantics |
|---|---|---|---|
| 0–1 | `world_seq` | uint16, 2 | Sequence; increments per generated World State, wraps at 65535 |
| 2 | `topology_id` | uint8, 1 | **Approved effective zone-availability bit mask**, above |
| 3 | `movement_command_1` | uint8, 1 | Car 1 movement flags |
| 4 | `movement_command_2` | uint8, 1 | Car 2 movement flags |
| 5 | `movement_command_3` | uint8, 1 | Car 3 movement flags |
| 6 | `movement_command_4` | uint8, 1 | Car 4 movement flags |

**BLE size: exactly 7 bytes.** Python format: `<HB4B`.

**PC→modem USB line:** literal uppercase `W` followed by **14 hexadecimal digits**, then LF (`\n`); **16 bytes per line**. The modem strips `W` and hex-decodes seven bytes before sending them via BLE. Do not send raw seven-byte binary directly over the USB COM link. See canonical spec for CRLF, invalid framing and debugging-text behavior.

#### Existing movement-command flags (unchanged)

| Bit | Name | Existing meaning |
|---|---|---|
| 0 | `auto` | 1=AUTO line following; 0=MANUAL wheel motion |
| 1 | `forward` | AUTO: forward speed enabled; clear bit to stop movement, without leaving AUTO |
| 2 | `backward` | MANUAL backward command; ignored in AUTO |
| 3 | `turn_left_indicator` | Existing left selection/steering bit |
| 4 | `turn_right_indicator` | Existing right selection/steering bit |
| 5–7 | reserved | Transmit zeros |

**Critical:** In AUTO mode, stop/neutral should preserve `auto=1`; no forward or turn bits gives movement byte `0x01`, not `0x00` (which selects MANUAL). The PC may change the *logic generating these bytes* without changing their defined wire meaning. New intersection-aware filtering and default-edge selection belong to PC business logic; the corresponding future local firmware enforcement is a separate approved work item.

### Robot State: exact existing wire layout

| BLE offset | Field | Type / bytes | Existing meaning |
|---|---|---|---|
| 0 | `robot_id` | uint8, 1 | Logical car ID 1–4 |
| 1–2 | `telemetry_seq` | uint16, 2 | Car telemetry sequence |
| 3–4 | `last_world_seq` | uint16, 2 | Most recent correctly sized World State received |
| 5 | `world_packets_lost_5s` | uint8, 1 | Car's completed five-second sequence gap measurement |
| 6–9 | `nfc_marker_id` | raw bytes, 4 | Last successfully scanned **4-byte UID**; retained after leaving tag |
| 10–11 | `nfc_marker_age_ms` | uint16, 2 | Age since last successful read; saturates at 65535 |
| 12 | `line_sensor_mask` | uint8, 1 | Eight line sensors, bit 7 leftmost |
| 13 | `controller_state` | uint8, 1 | AUTO / MANUAL / NFC recovery / battery / STOPPED |
| 14 | `line_follower_state` | uint8, 1 | Center/left/right edge, line lost, etc. |
| 15–16 | `left_speed_mm_s` | int16, 2 | Measured left wheel speed |
| 17–18 | `right_speed_mm_s` | int16, 2 | Measured right wheel speed |
| 19–20 | `battery_mv` | uint16, 2 | Filtered battery voltage |

**BLE size: exactly 21 bytes.** Canonical Python format: `<BHHB4sHBBBhhH`.
The three consecutive `B` fields after `nfc_marker_age_ms` represent
`line_sensor_mask`, `controller_state`, and `line_follower_state` at offsets
12, 13, and 14. This matches E15's packed `Ble14::Robot` structure and its
`static_assert(sizeof(Robot) == 21)`; `struct.calcsize("<BHHB4sHBBBhhH")` is 21.
E15 includes this shared structure from `src/e13/include/ble14_protocol.h`
and sends `sizeof(packet)` in `src/e15/car/ble_telemetry.cpp`. The former
20-byte Python packing string was a documentation error; this correction
does not change the firmware, fields, byte offsets or wire protocol.

**Modem→PC USB line:** 42 hexadecimal digits + LF, **43 bytes per line**, with no message header. Reject lines with unexpected length or invalid hexadecimal characters; debug/boot messages may occur.

### Sequence echo / topology acknowledgment (PC policy only)

- On changing topology, PC saves the sequence number of the **first World State containing the new topology byte**.
- Car telemetry echoes `last_world_seq`; the PC uses it to determine whether that car has received this new state or a later one. Comparison must accommodate **16-bit wrap**, duplicate/stale telemetry, reconnects and sequence reset ambiguity; don't use ordinary unbounded `>=` comparison.
- For the intended new firmware, the user elects to treat receipt acknowledgment as sufficient to consider topology applied; this assumes the car consumes topology immediately when receiving a World State.
- The echo is **not** evidence that the car physically cleared a zone; car-zone reservation and admission are handled by the game controller's NFC-derived state.
- Only applicable Present cars are synchronization participants, subject to the explicitly agreed last-known-outside exception for missing telemetry in `SYSTEM_SPEC.md`.
- Do not introduce new acknowledgments, new headers, a version counter field or a revised wire format without user approval. `world_seq` already exists and fulfills the intended correlation function.

### Existing E15 firmware constraints to respect

- E15's `serviceWorldState()` currently reads a car-specific movement command; `topology_id` is not currently enforced in car navigation.
- E15 currently validates a fixed **eight-tag** forward cycle. The future physical exhibit will use a different graph, repeated intersection-approach markers and many outside markers. Such changes are **out of scope here** until separately authorized.
- Its current timeout stops car motion after **500 ms without a valid World State**, while keeping its mode; other recovery/battery behaviors are specified in the canonical documentation and source.
- The E15 firmware's current `nfc_marker_id` is **four bytes**, not a generic 7-byte NFC UID field. Proposed handheld-registration designs must respect that limitation or be separately approved to change interface/firmware.
- The E15 BLE modem used in this repository is based on BLE, not ESP-NOW. No PC-side plugin should attempt to communicate with cars by ESP-NOW.

## Other device contracts (not part of frozen car payload)

**Joystick controller:** the single **USB HID gamepad tested in E2**, read through Pygame's joystick API. It supplies four sets of `(forward,left,right)` buttons. Poll it at **50 Hz**, in the same PC tick as World State generation/submission, independently of display redraw. Use E2's lifecycle handling as the baseline, but not its experimental 100 Hz cadence. There is no joystick COM transport or serial framing to specify.

Read zero-based button indexes from `config/address.ini`:

| Car section | Forward | Left | Right |
|---|---|---|---|
| `car_1_control` | 0 | 1 | 2 |
| `car_2_control` | 3 | 4 | 5 |
| `car_3_control` | 6 | 7 | 8 |
| `car_4_control` | 9 | 10 | 11 |

These are the current values of `forward_button`, `left_button` and `right_button`, not hardcoded application constants. Validate that the selected HID device exposes every mapped index. Existing `[joystick] serial_port` / `pnp_device_id` values are legacy serial metadata and must not be treated as this gamepad's identity. Only one gamepad is connected: use E2's working default index 0, validate available mapped buttons, track the active instance ID for removal events and retry index 0 while disconnected. No GUID/serial-number identification scheme is required now. Discuss input reenable details when implementing this adapter's recovery. On input loss, suppress car movement as specified in `SYSTEM_SPEC.md` while continuing shared World State broadcasts and independent robot operation.

**UR robot:** UR10e on Windows-controlled network interface; real robot motion via validated target+derived approach primitives; see `SYSTEM_SPEC.md`. Do not assume an unconfirmed API, teach pendant program, URCap custom command protocol or automatic protective-stop reset policy.

**Screwdriver:** existing URCap and RS-485 route, previously tested by user. Keep tool communication separate from mechanical completion checks; actual command/status bytes must be sourced from its existing implementation and verified.

**Cameras/monitors:** reuse E9's working four-screen H.264/PyAV CPU-decoding and screen-attached GPU-rendering baseline. Endpoints and SDL display indexes are in `config/address.ini`; displays currently map cars 1–4 to 2, 3, 0, 1. Display targets of 30/60 fps are independent of the **50 Hz** input/World State loop. Final map/HUD and admin behavior remain to be designed; video latency or display failure must not block that loop.

**NFC map/registration:** future shared logical-marker dictionary used on PC and in future car firmware. The current BLE telemetry is still the 4-byte UID field defined above. The user will provide Rhino-distilled tag/property JSON; defer detailed map implementation until reviewing it. Use 2-3 redundant approach tags per split. A wireless handheld scanner is a **separate design workflow** requiring discussion/permission before any additional firmware or modem messages.

## Compatibility review required at M0
The agent must obtain the actual `remote_control_payload.md` text from the linked GitHub source, compare it with E15, and maintain a **verbatim frozen copy** for traceability. This companion file is an explicit delta and extraction for the PC app, not permission to override other fields. Any discrepancy must be listed for user review before coding against an invented interpretation.

## Agreed command arbitration and admin input
Preserve current E15 AUTO/MANUAL movement semantics, including simultaneous-turn handling. The PC filters incoherent physical input; no firmware tie-break change is requested. The canonical document's older both-turn AUTO description is not a reason to change tested firmware. Deliberate following-mode encoding is separate from raw contradictory button input.

Escape toggles admin mode; 1-4 select a car; Up/Left/Right provide AUTO control; held Left Ctrl selects MANUAL with Down/reverse available. Visitor inputs are ignored in admin mode, but video/UI and World State transmission continue, with Game Paused / Admin Control shown on visitor screens. Q requests the verified-HOME-gated exit. Bindings use `[admin_keyboard_shotcut]`. These are PC policies using existing packet fields, not new firmware commands.

Discuss recovery per component at implementation, preferring silent reconnect with diagnostics. Prioritize car charging/power-off/reappearance, without inventing session fields or clearing reservations on telemetry loss. A focused screwdriver experiment precedes production tool integration to establish actual commands/status.
