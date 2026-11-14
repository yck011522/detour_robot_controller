# Milestone audit — 2026-11-14

## Evidence and scope

Reviewed the milestone/spec/interface documents, current `game_controller/`
implementation, tests, map CSV, and robot/tool experiment records. Re-ran
`python -m unittest discover -s tests -q`: **56 tests passed**. This audit did
not connect to hardware, command motion, flash firmware, or verify the installed
car firmware build. The repository was clean before this audit's documentation
updates.

The operator reported successful Car 1 coin collection, successful intersection
filtering, and then confirmed the player-only filtering/admin bypass works
perfectly. These are operator-reported results from this conversation; no new
run log filenames, exhaustive intersection checklist, or latency measurements
were supplied. They establish acceptance for the tested setup, not four-car
coverage or robot integration.

## Current agreed behavior

- Map input is the supplied `asset/map_attributes.csv`, not a pending JSON
  contract. It contains 310 tags, all with registered UIDs and no duplicates;
  33 split labels, 50 merge labels, 11 zone approach labels, and 12 gold tags.
  Registration completeness does not prove every physical placement/label.
- Coins score 1 point for silver and 10 for gold (`video_id` nonempty), on
  each car's own track. Respawn is 120 seconds. Parameters are adjustable.
  Totals persist in `config/coin_count.ini`; availability resets at startup.
  Collection requires a distinct intervening observed UID before a return;
  admin driving also collects.
- Player AUTO applies the agreed split table, merge-edge forcing, and
  ordinary-tag center following. Split choice locks when NFC age is strictly
  greater than 100 ms (configurable), until a different UID is observed.
- Admin AUTO and MANUAL bypass tag steering restrictions, including safeexit
  forcing. Keyboard input does not overwrite the player's selection. Existing
  drive-stop/freshness/protection checks remain active.
- Player zone approaches currently always take safeexit. There is no token
  manager, board inventory, zone ownership, or production robot integration.
- The existing 7-byte World State and 21-byte telemetry formats are unchanged.
  Topology currently carries only the admin bit; no zone availability is asserted.

These decisions supersede the older JSON-only and admin AUTO steering text
in the original plan. After this audit the operator clarified that availability
changes must never redirect a locked branch. Admin remains unrestricted for
car rescue, with the operator visually checking zone collision. Do not add
automatic admin steering/zone-admission restrictions from the older plan.
Robot scheduling and evacuation behavior during admin still need review.

## Milestone status

| Milestone | Status | Work remaining |
|---|---|---|
| M0 — architecture/interface audit | Complete baseline | Record actual installed firmware identity for subsequent acceptance runs; refresh compatibility evidence if that build differs. |
| M1 — communication, joystick, diagnostics | Accepted/closed, 2026-11-09, one-car scope | Preserve reconnect/focus/stop regression coverage; expanded hardware load belongs to M8. |
| M2 — tag navigation | Core player filter implemented and operator accepted on current setup | Record a representative split/merge log and command/echo timing; broader map/car tests. `adminsplit` is absent from the supplied CSV and unimplemented; decide whether still needed. Zone-dependent cases move to M3. |
| Additional game feature — coins | Implemented and operator-tested with Car 1 | Other cars and long-duration scoring/persistence regression during M8. The operator confirmed success before the change from 90 to 120 seconds; no separate timed 120-second acceptance was supplied. |
| M3 — zone reservations/topology/evacuation | Not implemented | Ownership/tokens, board availability input, admission/release, closure-consistent command/echo handling, charging/reappearance reconciliation, evacuation and diagnostics. |
| M4 — scheduler/workflow dry-run | Not implemented in production | Three-board inventory, startup confirmations, least-recently-touched selection, transfer state machine, synthetic robot events, interrupted-operation bookkeeping and HOME-gated shutdown dry-run. |
| M5 — tool and UR primitives | Supporting experiments exist; production integration incomplete | Reuse and validate tool/motion primitives, teach all zone targets, validate fixed fixtures/payload/clearance/completion, integrate recovery and real robot execution after M3/M4. |
| M6 — visitor/admin UX | Substantial implementation complete | Production board/zone visuals, startup inventory and robot/fault/recovery controls, HOME-dependent quit UX, four-active-stream/control-load measurements. |
| M7 — map/registration workflow | Runtime CSV integration complete; authoring workflow not audited | Validate physical labels/placements and export stability. Confirm whether the external registration workflow already meets needs before building additional tooling. |
| M8 — full integration/endurance | Not completed | All cars/controls/streams, full map and robot/tool integration, charging/reconnect/fault scenarios, extended supervised operation and operator handbook/sign-off. |

## Important distinctions

The production package has no zone manager, board scheduler, RTDE adapter or
tool executor. Existing robot payload settings are data for experiments/future
integration, not evidence the game applies them.

M5 does not start from zero: E11 records a passed supervised tool test, and E13
contains pick/place code, offline checks and later live-cycle/drift analysis.
E13's README introduction still says the combined routine was not tested,
whereas its drift document analyzes a completed logged cycle. Treat that as
documentation needing reconciliation, not as production acceptance. The drift
and completion-check findings must be reviewed against the final fixed fixture
before promoting those primitives into the game.

The CSV includes `inzone`, but the runtime loader does not yet load it.
M3 must add it and validate it alongside `reservezone`/`safeexit`. No graph or
firmware tag dictionary is needed. No firmware work is authorized by this audit.

## Recommended next steps

**New prerequisite identified by the operator:** physical boards P/Q/R/S each
carry two NFC tags; three are active and one spare. Add an admin-confirmed,
persisted startup arrangement and a shared dynamic board-UID-to-zone-TID
resolver before M3/M4. The original static map lookup does not handle board
relocation. See the extended [dry-run plan](M3_M4_DRY_RUN_PLAN.md) for the data
model, required registration details, startup persistence and hardware tests.
**Subsequently implemented, 2026-11-14:** board questions, atomic state recall
and confirmation, and shared UID resolution use the scanned p1–s3 registrations
and numbered zone references. Known unused tags are ignored without clearing
navigation/zone context. All 74 offline tests pass; physical acceptance is
pending. See [the board guide](../docs/BOARD_STARTUP_GUIDE.md). M3/M4 remain deferred.

1. **Design and implement M3 independently of live robot motion.** Use an
   explicit operator/synthetic board-availability state and synthetic robot
   requests first. Establish the three-board inventory constraint when creating
   this input; do not assume all four zones contain boards.
2. **Add M4 dry-run scheduling and startup/shutdown workflows.** Exercise at
   least 20 transfers, held-board startup, denied robot requests, interrupted
   operations and blocked HOME shutdown before attaching real motion.
3. **Promote the validated M5 experiment primitives into production.** Review
   current experiment findings, teach/calibrate all targets, and perform
   separately authorized physical tests with the final fixtures.
4. **Complete M6 state-driven UX alongside M3–M5**, then M8 multi-car/load and
   endurance acceptance. Audit M7's external authoring workflow in parallel
   only if additional registration tooling is needed.

M3 design interactions, updated after operator clarification:

- **Locked choice versus changing admission — confirmed:** later availability
  changes never redirect a locked branch. A locked safeexit remains safeexit;
  an admitted entry retains its token so the scheduler cannot reclaim that zone
  until release. Final grant/commit event ordering belongs in the M3 design.
- **Admin recovery — confirmed:** retain direct admin rescue control, including
  zone entry. The operator visually checks collision; do not implement the
  older automatic admin admission restriction. Continue recording context and
  ownership, without claiming automatic collision prevention in admin.
- **Evacuation:** reconcile the old forced-exit behavior with a locked branch,
  and decide how its 10-second inactivity/60-second hold timers behave in admin.
- **Charging/reappearance and unknown location:** retain reservations on loss,
  distinguish no-read from an observed unknown UID, and define reconciliation
  without inferring physical clearance from command echoes.

The next implementation milestone requires the normal short design review;
this audit records status and dependencies and does not start M3 implementation.
The proposed robot-free approach is described in
[the M3/M4 dry-run plan](M3_M4_DRY_RUN_PLAN.md).
