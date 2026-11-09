# Proposed E16: map-independent car firmware

Date: 2026-11-09. **Change brief only; no firmware implementation or flashing
is authorized by this document.** Create E16 from verified E15 when separately
approved; preserve E15 as the comparison baseline.

## Purpose and verified obstacle

The PC interprets Rhino-exported tag attributes and submits filtered movement
bytes at 50 Hz. Cars need no graph, UID dictionary, split/merge logic, token
logic or topology enforcement. Editing tag data must not require reflashing.

Reviewed local source:
`C:/Users/scmuser/Documents/line_follower_control/src/e15/car/main.cpp`.
Recheck the actual baseline at implementation; this audit does not identify
which revision is flashed onto every car.

- `kTrackUids` defines a fixed eight-tag cycle; `trackUidIndex()` looks up UIDs.
- `serviceNfc()` updates UID/time, then invokes `NFC_REINIT_INVALID_TRANSITION`
  for unknown or out-of-order tags, with `lastTrackUidIndex`, `rejectedUid`
  and `ignoreRejectedUid` bookkeeping. This conflicts with the new PC policy.
- `serviceWorldState()` already applies the car-specific movement byte and
  does not enforce topology. Its World State-loss stop is already 500 ms.

## Required minimal delta

1. Remove fixed-cycle/UID-whitelist navigation validity checks. Every successfully
   read supported four-byte UID updates retained UID/read time, regardless of
   registration, order, repetition, skipped tags or revisits.
2. Stop triggering recovery solely for unknown/out-of-order UIDs. Remove unused
   tag-cycle bookkeeping. Preserve telemetry state-code numbers; leave any
   unused legacy code reserved rather than renumbering others.
3. Preserve NFC initialization, cooperative servicing, bus-error recovery and
   periodic hardware recovery except logic strictly tied to tag-order checks.
   Preserve line-loss, low-battery, motor-control and recovery protections.
4. Preserve AUTO center/left/right edge selection, forward enable, MANUAL/reverse
   and tested simultaneous-turn handling. No local branch latching, tag lookup,
   token arbitration or admin topology behavior: PC supplies the effective bits.
5. Preserve the 500 ms World State-loss stop and existing recovery semantics.
   No NFC-progress watchdog: long gaps between reads are normal. This watchdog
   does not protect against a PC streaming inappropriate but valid commands.

## Unchanged interface

- World State `<HB4B`: exactly 7 bytes; existing fields/offsets/movement bits.
  `topology_id` remains PC-computed information, not firmware navigation logic.
- Robot State `<BHHB4sHBBBhhH`: exactly 21 bytes. Preserve four-byte UID,
  last-read age/saturation, state codes, wheel speeds, battery, sequence echoes
  and packet-loss reporting.
- Retain last UID after leaving a tag; repeated reads can refresh age. PC handles
  distinct-UID transitions, including revisits after an intervening UID.
- USB framing, BLE services/scheduling and modem firmware remain unchanged.
  No new messages, fields, counters, UID widths or acknowledgments.

## PC responsibilities

SYSTEM_SPEC sections 5-7 define all seven attributes, default edge selection,
merge forcing, adminsplit restrictions, zone admission in normal/admin modes,
continuous denied-token retries and evacuation. None belongs in E16 firmware.

A new observed UID with both `inzone:none` and `reservezone:none` releases the
token. An observed unknown UID has all-none attributes and qualifies. Startup/
no-read sentinels, telemetry loss or malformed frames do not constitute unknown
tag reads. Verify existing UID/age sentinel behavior without adding wire fields.
PC reboot/reappearance/localization and retained-token reconciliation remain
component-specific recovery discussions.

## Acceptance before adoption

Use a minimal direct script with a real car before adding wrappers; record the
exact source/build, hardware and logs.

- Compare E15/E16 packet sizes, offsets, state numbers and framing; verify the
  existing modem unchanged.
- Read arbitrary registered/unregistered UIDs, repeats, skipped/out-of-order
  tags and revisits without map-validity recovery or semantic motor stop.
- Verify AUTO center/left/right, stopped AUTO and MANUAL/reverse, plus battery,
  line-loss and NFC hardware-recovery behavior.
- Verify command loss stops motion at the existing timeout and fresh-command
  behavior still matches E15.
- With PC filtering, test a real split/default edges, merge, normal/admin
  adminsplit, denied zone admission even in admin, same-tag reservation retry,
  known/unknown release and approach/inside evacuation.
- Measure actual UID-to-PC-to-command/echo latency at operating speed/tag spacing.
  A nominal 50 Hz PC loop does not guarantee 50 Hz BLE delivery or branch success.

No firmware build, flash or live movement was performed to write this brief.
