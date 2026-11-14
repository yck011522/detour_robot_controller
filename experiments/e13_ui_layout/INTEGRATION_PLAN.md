# E13 UI integration plan

Updated 2026-11-14 following the user's scope clarification. This is a plan to
integrate the accepted E13 UI into the existing game controller. Game mechanics
are not prerequisites and are not implemented by this work. The asset relocation is complete, and cache preparation/validation now lives in
`game_controller/minimap_cache.py`. E13 rebuilds stale/missing caches at startup.
Production display/control integration and runtime renderer promotion are still
pending; control rules and firmware are unchanged.

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

Asset relocation is complete; the production `game_controller/ui/` code below
is still proposed:

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
  ui/                         # Production-owned settings, asset loader, renderer,
                              # minimap compositor, and UID-to-visual-tag lookup
```

The three pause PNGs are already in root-level `asset/` and can keep their
current names and locations. The accepted HUD frame, input-button assets, and all six split-panel variants
are now in `asset/ui_layout/`, preserving their names. The simulator photograph
is now `asset/test_background/e13_reference.png`; it is not a live-video asset.

The existing minimap cache and manifest are now in `asset/cache/minimap/`.
E12 loaders and the E13 simulator use that shared location. The production-owned
`game_controller/minimap_cache.py` owns rebuilding and freshness checks; E12
benchmark preparation delegates to it, and E13 checks it before loading sprites. The
simulator still imports E12 minimap code; promote that reusable code into the
production package during integration, so the controller has no experiment
code dependency. Native-resolution cached sprites remain for existing benchmarks.

Asset-generation tools, Tkinter simulation, example images, verification scripts,
and benchmark harnesses can remain in experiments/tools. After promotion, E13
should exercise the production renderer and root-owned assets, rather than
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

## Implementation sequence and function responsibilities

1. Asset relocation is complete. Next promote reusable HUD/minimap code into
   production-owned modules, and update the E13 harness to use those components.
2. Connect typed snapshots from existing accepted telemetry, inputs, presence,
   and CSV localization. No new control eligibility checks or game mechanics.
3. Integrate the cached HUD into the existing display/SDL path, preserving admin
   and capture behavior. Add adjustable wash and correct alpha blending.
4. Verify programmatically, then measure the actual camera/display path and
   four-display performance separately from the 50 Hz control loop.

| Function | Responsibility |
|---|---|
| `build_display_snapshot` | Map existing controller state to typed HUD fields, shared locations, and presence. |
| `resolve_tag_location` | Resolve CSV registrations to visual tags and last-known display locations only; never decide driving eligibility. |
| `load_layout_assets` | Load production-owned artwork/fonts once; no experiment paths or test backgrounds. |
| `compose_minimap` | Draw known, present car markers and supplied map layers using cached stacks. |
| `compose_layout_overlay` | Render supplied HUD data and the accepted pause artwork/priority. |
| `update_overlay_texture` | Upload changed HUD pixels with premultiplied blending in the SDL owner process. |

There is intentionally no `record_tag_visit`, persistence writer, respawn timer,
zone scheduler, or navigation-policy function in this UI integration plan.

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
