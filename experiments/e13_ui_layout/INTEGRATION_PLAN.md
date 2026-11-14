# E13 UI integration plan

Updated 2026-11-14: production integration is implemented using two new modules:
`game_controller/ui_layout.py` (settings, asset loading, HUD) and
`game_controller/minimap.py` (CSV registrations and cached map composition).
E13 now imports these production renderers. The existing app/display/video modules
provide numeric snapshots, shared last-known locations, premultiplied SDL blending,
changed-only overlay uploads, and the optional video wash. Control rules and
firmware are unchanged. Game mechanics below remain deferred.

The software checks cover E13's visual behavior and the production data/rendering
path. Live camera/four-display performance still requires hardware verification;
60 FPS is a pacing target, not a measured four-display result.

## Agreed scope

- Reuse the accepted 1920 ? 1080 HUD and 700 px minimap on the four existing
  dedicated car displays, with each display highlighting its own car.
- Connect the HUD to existing camera health, accepted telemetry, raw inputs,
  presence flags, and registered tag locations.
- Move the runtime artwork currently inside E13 into root-level `asset/`, and
  place runtime UI code inside `game_controller/`. Production code must neither
  import code nor read caches/artwork from any `experiments/` directory.
- Keep the existing camera decoding, SDL video path, admin controls, recording,
  keyboard routing, shutdown behavior, and independent 50 Hz control loop.
- Make the live-video gray wash adjustable, initially disabled, for later tuning
  against real footage. Keep the accepted translucent HUD panel styling.

## Explicitly deferred game mechanics

These features must not block UI integration:

- Coin collection rules, visit counting, persistent totals, and the future
  settings-like file used to retain totals across controller runs.
- Coin disappearance and reappearance after roughly 60 seconds. That timing is
  future game behavior, not a timer to add during UI integration.
- Zone reservation, robot ownership/arbitration, topology changes, navigation
  policy, evacuation, and any movement-command filtering associated with them.
- NFC registration tooling and completion of the CSV's UID registrations.

The UI will retain inputs for future coin totals, visible coins, split selection,
and reserved zones. During this integration, the coin number can display a
clearly defined initial value of 0 without counting or persisting anything;
coin markers keep their current static visibility. Zone overlays remain absent
until actual reservation state is supplied. The split panel consumes an
explicit split/selected-exit pair when available, and remains hidden otherwise.
Do not put branch-selection or reservation decisions inside the renderer.

## Root-owned runtime code and assets

Runtime assets and renderers are production-owned:

```text
asset/
  ui_layout/                  # Accepted HUD SVG/PNG artwork (moved)
  cache/minimap/              # Shared 700 px cache/manifest plus native benchmark variants (moved)
  test_background/             # Includes e13_reference.png for the simulator (moved)
  Font/                       # Existing Orbitron files and license
  map_attributes.csv          # Swappable tag/UID data
  Game Paused Battery Low.png
  Game Paused Telemetry Lost.png
  Game Paused Line Lost.png
  ...existing map source PNGs...

game_controller/
  ui_layout.py                # Settings, artwork, typography, and HUD composition
  minimap.py                  # CSV registration loading and minimap composition
  minimap_cache.py            # Existing cache preparation and freshness checks
```

The three pause PNGs are already in root-level `asset/` and can keep their
current names and locations. The accepted HUD frame, input-button assets, and all six split-panel variants
are now in `asset/ui_layout/`, preserving their names. The simulator photograph
is now `asset/test_background/e13_reference.png`; it is not a live-video asset.

The existing minimap cache and manifest are now in `asset/cache/minimap/`.
E12 loaders and the E13 simulator use that shared location. The production-owned
`game_controller/minimap_cache.py` owns rebuilding and freshness checks; E12
benchmark preparation delegates to it, and E13 checks it before loading sprites. The
simulator and controller now import `game_controller/minimap.py`; production has
no experiment code dependency. Native-resolution cached sprites remain for existing benchmarks.

Asset-generation tools, Tkinter simulation, example images, verification scripts,
and benchmark harnesses can remain in experiments/tools. E13
now exercises the production renderer and root-owned assets, rather than
maintaining a second implementation. Production must never import E13 or E12.
No runtime SVG rasterization is needed: load the prepared transparent PNGs once.

## CSV replacement and marker behavior

- Read `asset/map_attributes.csv` on controller startup and resolve registered
  four-byte NFC UIDs to TIDs, floor, track, and the relevant visual attributes.
- Use the registrations that exist now. The currently incomplete registrations
  are not an integration gate and must not lead to hardcoded test-tag mappings.
- The user will replace the CSV with the completed data using the same schema.
  Re-read it on the next controller start; live file hot-reload is not required.
- If a car has no known registered location, hide only its marker and show an
  unknown floor as `--`. Do not assign a simulator starting tag.
- Preserve an existing last-known registered marker through temporary telemetry
  loss, without interpolation. A marker is hidden if no known location exists
  or the car is marked Not Present. This is display state, not driving permission.
- **An unlocalized car must remain controllable under the controller's existing
  command rules.** Missing UID registration must not introduce a stop condition,
  input gate, recovery requirement, or altered movement byte.

## Presence and pause overlays

For cars marked **Not Present** by admin:

- Hide their car markers from every minimap, including their own display.
- Show the existing **telemetry-loss warning overlay** to players on that car's
  dedicated display. This replaces the earlier proposal to suppress that warning.
- Keep presence and actual telemetry health as separate values: Not Present can
  request the warning artwork even if packets are still arriving. Do not falsify
  the received telemetry state or change existing presence/control semantics.

For other cars, keep the accepted pause priority:

1. Telemetry lost, based on the existing telemetry freshness check.
2. Low battery, reported by fresh telemetry controller state `14`.
3. Line lost, reported by fresh telemetry follower state `10`.

Not Present requests the same top-priority telemetry-loss artwork. Do not add a
battery-voltage threshold or infer line lost from the raw sensor mask. Continue
retaining the last battery and sensor pattern when telemetry is stale, with
wheel speeds shown as dashes. Keep updating video/UI while pause artwork is shown.

## Display data mapping

| Display input | Source / handling |
|---|---|
| Fixed car number | Assigned display worker's car ID 1?4. |
| Wheel speed | Accepted `left_mm_s` / `right_mm_s`, divided by 1000. Dashes when stale. |
| Battery | Accepted `battery_mv`, divided by 1000. Last known value when stale; unknown before the first packet. |
| Eight sensor dots | Accepted `sensors`, bit 7 at left. Retain the last accepted pattern when stale. |
| Pause state | Existing telemetry health, presence, and reported controller/follower state; rules above. |
| Video blink | Last decoded non-corrupt camera frame and existing `video_stale_s`. |
| Raw input buttons | Existing raw forward/left/right booleans, separate from effective commands. |
| Car markers / floor | CSV UID-to-TID mapping and shared last-known registered positions, filtered by presence. |
| Coin number / visibility | Display inputs for later game logic; no counting, storage, or respawn in this change. |
| Split panel | Display an explicitly supplied split/selected-exit pair; no renderer-owned navigation policy. |
| Zone overlays | Display explicitly supplied reservation/ownership state later; no reservation logic in this change. |

Build typed display snapshots instead of parsing the current human-readable
battery/sensor/status strings. Every display needs the shared four-car locations
and presence flags for its highlighted/ghost minimap. Retain accepted packet
ordering and freshness handling; do not change the wire format.

## Live-video presentation and rendering

- Add a tunable UI setting such as `video_wash_alpha` in the existing INI
  configuration: 0?255, initially **0**. The wash color can remain neutral gray;
  this setting does not change the HUD's own transparency or minimap floor alpha.
- Continue using the current YUV/SDL video rendering path. Compose video ?
  optional wash ? visitor HUD/minimap ? car pause artwork ? existing admin,
  capture-menu, and quit-confirmation UI.
- E13 outputs premultiplied alpha. Use the corresponding SDL blend mode rather
  than the current ordinary-alpha mode, which would darken translucent edges.
- Retain cached floor stacks and text. Rebuild the map/HUD only when relevant
  display state changes; upload the completed overlay texture only when needed.
- Target 60 display FPS, keeping the control loop at 50 Hz. A 50 Hz typed UI
  snapshot rate can carry existing input/telemetry updates without coupling
  control to rendering; measure that overhead before claiming the target met.
- Preserve recording with and without the complete UI, including the chosen
  wash/overlay appearance for the with-UI capture. E13's Escape-to-exit behavior
  remains simulator-only; production keeps its existing exit controls.

## Implemented function responsibilities

| Location / function | Responsibility |
|---|---|
| `app.build_display_snapshot` | Map accepted telemetry and shared visible locations to numeric HUD fields. |
| `minimap.load_tag_attributes` | Read CSV visual tags and NFC registrations at startup. |
| `minimap.load_minimap_assets` / `load_cached_layers` | Load cached sprites and group static coins once. |
| `minimap.compose_minimap` | Render known, present markers and cached floor stacks. |
| `ui_layout.load_layout_assets` | Load production artwork/fonts once; simulator backgrounds are opt-in. |
| `ui_layout.compose_layout_overlay` | Draw supplied HUD values and pause priority. |
| Existing `display.play` / `video.VideoWindow.draw` | Compose video wash, HUD, and admin UI; upload changed premultiplied pixels and preserve capture modes. |

UID resolution and last-known locations are maintained beside accepted-packet
handling in `app.py`. No additional location service, renderer class, compatibility
layer, or game-mechanics module was added.

## Verification completed on the development computer

- All 35 tests in `tests/` pass, including production HUD/CSV/marker tests and
  existing controller, capture, monitor, and cache checks.
- `verify_layout.py` passes using the promoted renderer; all 691 original
  artwork/reference hashes remain unchanged.
- Hidden-window synthetic YUV tests pass for both SDL `direct3d` and `direct3d11`:
  raw captures retain the video, with-UI captures blend premultiplied pixels
  correctly, and unchanged overlay textures are reused.
- A rendered production snapshot was inspected visually.

The development computer is not the production four-monitor/two-GPU setup.
Monitor assignment, live-camera recording, and sustained four-display performance
are left for the user's later production hardware test. No robot movement or
serial/camera connection was used for these checks.

## Acceptance checks

Use headless/exported-image and scripted-state verification, without desktop
mouse automation, for:

- Existing labels, positions, fonts, gray sensor circles, pause transparency,
  priority, and per-car isolation.
- Real telemetry unit conversion, stale dashes, and retained battery/sensors.
- An unregistered car hiding its marker without changing its command output.
- A Not Present car hidden from all minimaps while its dedicated screen shows
  the telemetry-loss artwork, independently of whether telemetry is fresh.
- Replacing the CSV and restarting to pick up newly registered UIDs without
  editing code or changing control behavior.
- Production imports and runtime asset/cache paths remaining free of any
  `experiments/` dependency.
- Adjustable video wash, existing admin/capture/quit behavior, and recordings
  both with and without UI.

Then test one live camera/car display and all four displays, measuring rendering
and 50 Hz control timing. Earlier one-instance synthetic benchmarks do not prove
four-display 60 Hz. No live movement is required for the UI acceptance checks.

Coin persistence/counting, roughly 60-second coin reappearance, reservations,
navigation mechanics, robot scheduling, and firmware work remain separate tasks.
