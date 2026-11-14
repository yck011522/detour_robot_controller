# Board startup and dynamic NFC assignment

Implemented 2026-11-14. This covers board-state questions and tag resolution;
zone reservations, scheduling and robot movement remain deferred.

Player zone admission now uses this confirmed layout: a board in a zone enables
normal branch selection; an empty zone forces safeexit. A board held by the
gripper or spare does not enable any zone. Choices still lock above 100 ms NFC
age, and later availability changes never redirect a locked branch. Admin
steering remains direct. This is board-presence filtering, not robot reservation.

## Startup

Launch `python -m game_controller` as usual. All four displays show the same
questionnaire over updating video/HUD. Every car stays stopped until the layout
is valid, saved and confirmed. Space cannot enter player mode during questions.

- **1–4** selects Board P/Q/R/S; **Up/Down** also selects a board.
- **Left/Right** cycles unassigned, A, B, C, D, gripper, spare.
- **Enter** validates and saves; **Q** opens the usual quit confirmation.

Each board appears once, each zone/gripper holds at most one board, and exactly
one board is spare. Three boards are active across zones plus gripper. First
launch leaves P/Q/R unassigned and defaults S to spare; no physical arrangement
is assumed. Another board can be selected as spare if needed.

`config/board_state.ini` is created after confirmation. Later launches prefill
the last layout but still require visual confirmation. Saving runs outside the
control tick and replaces the INI atomically. Failure keeps driving inhibited
and shows an error; Enter retries. This increment cannot move boards or edit
assignments during a run, so the confirmed file also represents its last known
layout at shutdown. After manual relocation, restart and correct the form.

With `--no-video`, console questions appear after initial device connection and
before the live tick starts. No driving commands have been submitted during
those prompts. Enter retains a recalled value; Y confirms the summary; Q or
Ctrl+C cancels. Normal display mode keeps 50 Hz stop-only submissions running
while the form is open.

## Mapping

Physical registrations come from `config/dynamic_nfc.ini` (p1–s3). The map CSV
retains these destination references:

| Zone | Destination references |
|---|---|
| A | T73 `<a2>`, T63 `<a3>` |
| B | T76 `<b1>`, T68 `<b2>` |
| C | T72 `<c2>`, T62 `<c3>` |
| D | T77 `<d1>`, T69 `<d2>` |

P in C binds p2 to T72 and p3 to T62. P in B instead binds p1 to T76 and p2 to
T68. Neither the CSV nor scanned registrations are rewritten. Navigation,
minimap and coins share the resolver; TIDs retain their location attributes.
Coins/cooldowns belong to destination TIDs; remapping itself awards nothing.

Known board tags without a slot in the confirmed layout are **ignored**. This
includes unused numbered tags and tags on held/spare boards. They do not change
the previous navigation UID, intersection/zone context, marker or coin visit,
and cannot trigger a future reservation release. Raw telemetry still updates
health/diagnostics and existing context age advances. Logs show `NFC_IGNORED`.
Truly unregistered observed UIDs still clear context to ordinary road; no-read
UIDs preserve it. These outcomes are explicitly distinct in the resolver.

## Verification

All **74 offline tests** pass, including all 240 valid board arrangements,
actual registrations/references, state recall/save errors, startup driving
inhibition, form rendering, remapping/coin eligibility and ignored-tag retention.
No physical hardware run was performed for this implementation.

Confirm the actual layout, drive over both used tags and the unused tag of a
board, and check destination TIDs and ignored-tag retention. Restart to verify
recall. With driving stopped, manually relocate a board, restart/correct its
location and repeat. Confirm all zone/branch/coin behavior on the physical setup.
