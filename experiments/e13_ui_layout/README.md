# E13: UI layout experiment

A single windowed **1920 × 1080** Pygame preview based on
[`reference/layout.svg`](../../reference/layout.svg), with a separate Tkinter
simulator. This experiment uses the production **700 px cached minimap** and HUD. It does not
connect to cameras, serial, cars, or robots, and does not modify the source
artwork, fonts, E12, or the main application.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe experiments/e13_ui_layout/ui_layout.py
```

Requires the existing Pygame dependency and Windows Python's Tkinter. Uses the
provided static Orbitron Regular/Bold fonts and installed Microsoft JhengHei /
Segoe UI for Chinese and secondary labels. No additional Python packages are
needed. E13 checks the shared minimap cache before loading it. On a new computer,
a missing cache is built automatically; a changed map PNG, target size, cache
format, missing sprite, or invalid/missing manifest/build information triggers
rebuilding. First preparation can take a few minutes, with console progress.
Unchanged caches are reused. CSV registration edits and floor-opacity settings
do not require rebuilding raster sprites.

## Folder structure and integration review

The launch command is unchanged. Existing code is now separated by responsibility:

| File | Purpose |
|---|---|
| `game_controller/ui_layout.py` | Production settings, artwork, typography, telemetry, and pause overlays. |
| `game_controller/minimap.py` | Production CSV loading and cached four-car minimap. |
| `layout_settings.py` | Simulator paths and imports of production visual settings. |
| `asset_tools.py` | SVG/PNG generation through `--prepare-assets`. |
| `simulator.py` | Tkinter controls, production minimap preview, snapshots, and benchmarks. |
| `ui_layout.py` | Small CLI entry point. |
| `verify_layout.py` | Programmatic image/state checks. |

The renderer and default asset loader do not import Tkinter or E12 and do not
load test backgrounds. The simulator explicitly opts into those backgrounds.
Unused sensor PNG/SVG artifacts and their generator code have been removed.
The production game controller uses these same rendering functions.

See [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) for the agreed UI-only integration
scope, root-owned asset/code destinations, typed data mappings, marker/presence
behavior, and acceptance checks. Coin and reservation mechanics are deferred
and do not block the UI integration. These are planned production changes; the
current E13 experiment remains a simulator.

## Controls and behavior

- Four car rows choose the active display, independent tag positions, and each
  car's displayed coin count. `Scan +1` simulates one counted scan; counters
  remain editable and are not persisted or connected to actual NFC events.
- Each car row also has **Low battery**, **Telemetry lost**, and **Line lost** test flags. The
  selected car's flag displays its supplied full-screen transparent PNG on top
  of the HUD. Flags on other cars do not pause the selected view. Telemetry lost
  takes priority, followed by low battery, then line lost. Low battery is an explicit simulator flag,
  not an automatic voltage threshold. Unchecking the shared `Telemetry fresh`
  control also displays the telemetry-lost overlay on the selected view.
- **Line lost** simulates the telemetry-reported line-lost state independently
  of the eight raw sensor dots; it is not inferred from a zero sensor mask.
- Current floor comes from the selected car's tag, displayed as `-1`, `G`, or
  `1F`–`7F`. Its floor badge is orange. E12's current/other floor opacity settings
  and existing floor/car/coin rules are reused.
- Four checkboxes simulate robot zone reservations.
- `Video present` simulates video health: the dot alternates orange and muted
  gray-red every 0.5 seconds when enabled, and remains gray-red when disabled.
  Background animation is independent of this simulated health state.
- Forward/left/right checkboxes visualize raw inputs, independently of the
  selected split exit. These controls never command a car.
- While `Telemetry fresh` is enabled, wheel speeds, battery, and eight sensor
  checkboxes are accepted as telemetry. Bit 7 is the leftmost sensor.
- Detected sensors are filled with the same gray as speed text; inactive sensors
  are hollow gray circles. Pygame draws the circle geometry directly, with 4×
  antialiasing in premultiplied alpha space, rather than loading sensor PNGs.
- When telemetry becomes stale, both wheel speeds show `--`; the last accepted
  battery number and sensor pattern remain visible. Editing the raw controls
  while stale does not change those retained values. Re-enabling freshness
  accepts the latest simulated values. The footer explicitly reports stale
  telemetry underneath the pause artwork. Real timestamp-based staleness is
  deferred to integration.
- Split modes: **left/straight**, **straight/right**, and **left/right**, each
  with either selected exit, plus **Hidden**. **Auto from tag** reads the CSV's
  `split` field (`left`, `right`, `leftright`, or empty). When changing the split
  type, the exit selector retains a valid exit or selects the first available
  one. This previews appearance, not navigation/reservation policy.
- Background choices include the photo extracted from the reference SVG and
  the existing test JPGs. `Animate test background` adds a gentle pan to test
  moving content. This is not a video decoder or network feed.
- `F12` in the Pygame window saves `results/interactive_preview.png`.
  Escape in that window, or closing either window, exits.

The simulator opens on car 01 at T101 (floor 1, track 2), deliberately showing
that a car can occupy another track. Initial sensors and input states resemble
the reference; the split panel initially previews left/right, right selected.

## Artwork choice

`asset/ui_layout/` contains paired **SVG sources and transparent PNG runtime assets**:

- HUD frame and icons.
- Forward, left, and right input buttons, each on/off.
- Six approaching-split panels, with bilingual labels and selected paths.

The reference photograph is extracted separately to
`asset/test_background/e13_reference.png`. The reference JPG's dummy minimap is
never used. HUD artwork and the shared minimap cache have moved to root-level
`asset/`; experiment outputs remain under `results/`. The three pause PNGs and
Orbitron fonts were already root-owned and remain at their existing paths.

SVGs are convenient editable vector sources; Pygame on this machine cannot load
them directly. PNGs retain transparency and are decoded once at startup. JPEG
is suitable for opaque photos but cannot represent transparent HUD artwork.
The generator uses the same vector primitives for SVG and PNG, renders PNGs at
3× resolution, and downsamples them for antialiasing. SVG text needs the stated
fonts installed in the editor; PNG text is already rasterized.

Regenerate the artwork after changing the generator:

```powershell
.\.venv\Scripts\python.exe experiments/e13_ui_layout/ui_layout.py --prepare-assets
```

`asset/ui_layout/` is the output of `prepare_layout_assets` in `asset_tools.py`; regeneration overwrites
manual edits there. Change `asset_tools.py` to preserve an artwork adjustment. Static runtime labels
and their positions are now in `game_controller/ui_layout.py`; theme/geometry settings are
in that production module.
E13 automatically rebuilds the shared minimap cache after map artwork changes.
To prepare it separately or force a rebuild:

```powershell
.\.venv\Scripts\python.exe -m game_controller.minimap_cache --force
```

Preparation/validation lives in `game_controller/minimap_cache.py`, without
experiment imports. E12's benchmark preparation command delegates to it too.
This writes `asset/cache/minimap/`. It is a rebuildable cache ignored by Git;
original map PNGs and the HUD SVG/PNG artwork are not ignored. Native cropped
variants remain alongside the 700 px variants for existing benchmark tools.

## Rendering and measurements

The native HUD coordinate system remains 1920 × 1080. The app targets 60 Hz.
Minimap work runs in one worker on map state changes; static floor stacks are
reused within a configuration. The last completed map remains visible until
the next composition finishes. The full HUD is rebuilt only when displayed
state, blink phase, or the completed map changes; other frames reuse it over
the background. Static labels and dynamic strings are cached, as are fonts.

The timing field reports the latest map composition, latest HUD rebuild,
current draw work, and current loop work, separately from refresh pacing.
Times are work durations, not input-to-display latency or physical scanout.
The app uses premultiplied alpha through composition and final blitting.

Export ten full-resolution example images and the six-panel contact sheet:

```powershell
.\.venv\Scripts\python.exe experiments/e13_ui_layout/ui_layout.py --snapshot
```

Export those images and run a local software-rendering benchmark:

```powershell
.\.venv\Scripts\python.exe experiments/e13_ui_layout/ui_layout.py --benchmark
```

Results are in `results/benchmark.json`. Each case warms 30 frames and measures
300 frames, reporting median/p95/max. The hidden single-window benchmark includes
background and HUD blits, plus display flip. One case reuses the HUD; the other
rebuilds it every frame with a warmed text cache. Timed loops exclude map
rebuilds, actual video decoding, transport, and physical display presentation.
They are not a four-display or production 60 Hz acceptance test.

Preview files:

- [Default reference-style preview](results/layout_leftright_right.png)
- [Stale telemetry preview](results/layout_stale.png)
- [All six split variants](results/split_variants.png)
- [Low-battery pause](results/layout_paused_battery_low.png)
- [Telemetry-lost pause](results/layout_paused_telemetry_lost.png)
- [Line-lost pause](results/layout_paused_line_lost.png)

These visual states and timing checks are the experiment's acceptance scope.
Actual exhibition counter persistence, scan semantics, live telemetry adapters,
camera health thresholds, and production GPU integration remain separate work.

## Programmatic verification without desktop interaction

```powershell
.\.venv\Scripts\python.exe experiments/e13_ui_layout/verify_layout.py
```

This uses SDL's dummy driver and in-memory simulator values; it does not open or
click desktop windows. It regenerates the example images, checks all six SVG/PNG
panel variants and selected-arrow pixels, validates transparency and dimensions,
and checks that the Orbitron exhibit title fits the header.

It then exercises the real refresh callback with scripted input values, checking
stale/fresh transitions, retained battery/sensor image regions, wheel-speed
dashes, all active car views, counters, CSV split selection, and redraw timing.
It also verifies video-dot blink phases and unchanged source-file hashes.
Results are saved in [results/verification.json](results/verification.json).
