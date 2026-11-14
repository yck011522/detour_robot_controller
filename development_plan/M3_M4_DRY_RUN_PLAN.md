# M3/M4 without a physical robot

Design proposal, 2026-11-14. No implementation or hardware action is performed
by this document. Taught poses are not prerequisites for the logical milestones.

**Implemented prerequisite, 2026-11-14:** startup board-state questions and
dynamic UID assignment are now implemented; see
[the operator guide](../docs/BOARD_STARTUP_GUIDE.md). Inputs are three tags per
board in `config/dynamic_nfc.ini`, eight slot references in the CSV and locations
in `config/board_state.ini`. Known unused tags are ignored, retaining the previous
navigation/zone context and coin visit. No M3/M4 implementation is implied.
This note supersedes the earlier two-tag/board_tags.ini proposals below.

## Confirmed decisions

- Availability never redirects a locked branch. Before locking, admission may
  affect selection; after locking, a safeexit choice remains safeexit. An
  admitted car retains its token until the observed release condition, so a
  robot request cannot revoke its committed entry.
- Admin is direct rescue control, with operator visual collision checking.
  Keep the steering/zone-entry bypass and existing drive-stop checks. Record
  occupancy/reservations; do not present admin operation as automatically
  collision-protected.

## Architecture

**Movable-board correction, 2026-11-14:** there are four physical boards P/Q/R/S,
each carrying two NFC tags. Three boards participate in the game, including
any board held by the gripper; one remains spare. Board identity travels with
the physical board. Zone/TID identity belongs to the fixed map position.
The existing static UID-to-TID mapping is insufficient for movable-board tags.
The design below is extended by the startup/remapping section at the end.

Keep zone ownership and board inventory in the central 50 Hz controller.
Implement a small zone-state module and a scheduler module independent of
Pygame, device I/O and robot coordinates. A dry-run robot adapter accepts named
operations against zone A/B/C/D and emits timed or manually advanced events.
It neither imports RTDE control nor opens robot/tool connections. It needs no
joint values, FK/IK or fabricated taught targets.

Use the same event contract for a later real adapter, for example:
`operation_started`, `pickup_completed`, `placement_completed`,
`high_clearance_reached`, `home_reached`, and `fault`. Events carry operation
identity so delayed/duplicate completions cannot change a newer operation.
Synthetic clearance and HOME events are explicitly labeled; they prove state
machine behavior, not physical clearance or homing.

## M3: zone logic first

1. Load/validate `inzone` alongside the existing approach attributes. Track
   board occupancy, each car's token, and robot pending/owned state per zone.
   Startup inventory contains exactly three boards across zones plus gripper.
2. Resolve car release/admission and robot requests in a deterministic tick
   order. Car 1 competes for A/B, Car 2 for C/D; Cars 3/4 never gain tokens.
   A board must be present for player admission. An owned car token blocks
   robot acquisition; it is not revoked by later availability changes.
3. Retry denied requests while the approach remains selectable. Once safeexit
   locks, do not turn a late grant into entry. Keep the branch unchanged until
   the next distinct UID. Maintain the established no-read/unknown distinction
   and observed release predicate: both `inzone` and `reservezone` empty.
4. For a robot request, close availability only if no car owns the zone. Publish
   closure-consistent steering and topology together; wait for the required
   fresh, wrap-safe car receipt echoes before the dry-run robot acquires it.
   Echoes remain receipt evidence, not proof of physical clearance.
5. Add ownership/availability diagnostics and explicit synthetic robot request,
   release and fault controls. Retain reservations across telemetry loss and
   test charging/reappearance reconciliation.
6. Add evacuation only after reviewing its interaction with an already locked
   branch and its timer behavior in admin. Never silently overwrite the
   confirmed locked branch to implement the earlier evacuation wording.

M3 testing uses injected telemetry/events first, with optional one-real-car
validation against a fixed, physically known board layout. Do not apply virtual
board removals as if they were physical changes in a real-car test. Show the
dry-run model separately from operator-confirmed physical inventory.

## M4: schedule the virtual transfers

Initialize board locations explicitly, for example A/B/C occupied, D empty,
gripper empty. Run the existing planned least-recently-touched queue. Pick an
eligible occupied zone; after pickup, choose an eligible empty placement zone
other than the known pickup source. Skip reserved zones without changing their
queue priority; update priority only after successful pickup/placement.

Drive the dry-run robot through the logical phases: HOME, request zone, wait for
ownership, approach, operation completion, retreat to high clearance, release
ownership, HOME. Pickup/placement completion updates board inventory; clearance
releases robot ownership; HOME enables the next scheduled operation. Do not use
one timer completion as proof of all three events.

Provide automatic timed progression and manual stepping/fault injection so we
can stop at any phase. A timeout/fault does not silently complete a transfer,
release an occupied zone, or infer whether a board is attached. Quit stops new
scheduling and exercises the synthetic clearance/HOME sequence; blocked HOME
keeps the dry-run application in its blocked-shutdown state.

## Acceptance and later integration

- Test competing requests, denied/retried requests before lock, late
  availability after lock, reservation release, sequence wrap, stale echoes,
  unknown/no-read tags, charging loss and direct admin rescue behavior.
- Run at least 20 repeatable synthetic transfers. Assert three-board
  conservation, no contradictory ownership in normal modeled operation,
  reserved-zone exclusion, held-board startup, and correct fault/shutdown state.
- Record deterministic event logs and unit-test scenarios. Keep synthetic and
  physical results distinct.
- In M5, teach real targets and replace synthetic events with verified motion
  and tool feedback through the adapter. Revalidate physical timing, completion
  and clearance; M3/M4 dry-run acceptance does not establish them.

Before implementation, finish the short design review for evacuation under the
branch lock, admin timer/scheduling behavior, and interrupted-operation recovery.
Those questions do not require taught poses or a connected robot.

## Startup inventory and dynamic NFC resolution

Implement this prerequisite before zone admission and automated transfers.

### Data and startup questionnaire

Propose `config/board_tags.ini` for the stable identities (two UIDs for each
board and two ordered destination TIDs for each zone), and
`config/board_state.ini` for persisted board locations/orientations and pending
operation information. These names/schema are proposals pending the mapping
details; no physical UID or TID assignment is guessed.

The admin startup questionnaire shows A/B/C/D, gripper and spare and prefills
the last saved arrangement. Require visual confirmation or correction on every
startup; saved state is not fresh physical evidence. Validate that every board
identity occurs once, each zone/gripper holds at most one board, exactly three
boards are active across zones plus gripper, and exactly one board is spare.
If installation can reverse tag order, record orientation for each placement.
Unknown attachment/location after an interruption requires admin reconciliation
before normal operation, not an inferred completion.

Persist confirmed startup changes, successful pickup/placement changes, and
clean shutdown. Also record in-progress operation identity/phase so an abrupt
exit does not masquerade as a confirmed completed transfer. Use atomic INI
replacement outside the control tick. Keep synthetic dry-run inventory in a
separate state file so simulation cannot overwrite the remembered real layout.

### Shared resolver

Keep the destination TIDs, artwork and attributes in the map CSV. Remove the
eight movable destination positions from the fixed UID lookup, then bind them
through the confirmed arrangement:

`board tag UID -> board + local tag index -> location/orientation -> destination TID`

The two tags of a board placed in A resolve to A's two destination TIDs; after
moving it to C, the same UIDs resolve to C's destination TIDs and attributes.
Fixed track tags retain their ordinary static mapping. Do not infer the eight
positions from `inzone`: the current CSV has 15 in-zone tags, some potentially
fixed. Require the actual movable-tag list and validate UID/TID uniqueness.

Navigation, minimap localization and coin scoring must consume this one
resolver. Currently app localization and CoinGame have independent static UID
lookups; both need changing. A board UID that is known but spare, held, in
transfer or unused in its zone has no valid track TID. Per the later operator
decision, ignore it without changing navigation/zone context, marker or coin
visit. It is not an unknown/release tag and earns no coin. Admin remains direct.

Pickup completion removes the board's map bindings; confirmed placement
completion installs the new bindings. Release robot ownership only on the
separate clearance event. Do not remap at operation request or planned target
selection. In physical tests these updates require physical/operator-confirmed
completion; dry-run events update only the synthetic inventory/resolver.

Proposed coin policy: coins/cooldowns belong to destination TIDs as today,
independent of which board occupies them. A remap alone is not a new car visit
and awards no points. This policy should be confirmed before implementation.

### Validation sequence

1. Synthetic tests cover all board/zone assignments, spare swaps, orientation
   where applicable, duplicate/missing registrations, startup validation,
   persistence and interrupted-operation reconciliation.
2. With a real car, verify real UIDs against an admin-confirmed fixed layout;
   manually relocate one board between runs/admin confirmations and verify
   both tags now resolve to the destination TIDs, attributes, artwork and coins.
3. Exercise M3 with real telemetry and synthetic robot requests while the
   physical board arrangement stays fixed and correctly represented. Temporary
   simulated closure is possible; synthetic placement must never open a route
   lacking a physical board.
4. Run M4 automated transfers with synthetic cars and synthetic inventory;
   manually stepped physical board changes are a later supervised extension.
   Robot poses remain unnecessary for all these logical checks.
