# E12: cached 700 px Pygame minimap

Run the single application entry point from the repository root:

```powershell
.\.venv\Scripts\python.exe experiments/e12_pygame_minimap/minimap.py
```

Requires Pygame and Python's Tkinter. No camera, NFC reader, or robot is used.
The application opens a 900 ? 800 Pygame preview and separate Tkinter controls,
with a 60 Hz refresh target. The former slow renderer and `cached_preview.py`
entry point have been removed.

Four rows each provide an active-car radio button, a tag slider, and tag/floor/
track details. All tags can be selected for any car, including another track's
tags and tags with no track. The active car is fully visible and the other cars
use 20% opacity. Four checkboxes simulate robot reservations for zones A?D.
The background selector chooses from `asset/test_background/bg1.jpg`?`bg4.jpg`.
Backgrounds fill the preview using centered cropping.

## Floor opacity

Edit the settings near the top of `minimap.py`, then restart:

```python
CURRENT_FLOOR_OPACITY = 0.90
OTHER_FLOOR_OPACITY = 0.20
```

Values from 0.0 to 1.0 multiply the artwork's alpha. Reserved zones follow these
settings. Track/coin opacity and the active/ghost car rules are independent.
Opacity changes are applied in memory at startup; no disk-cache rebuild is needed.

## Rendering and redraw time

Floors are visited in order `-1, G, 1, 2, 3, 4, 5, 6, 7`. On each floor the order
is floor ? reserved zones (G only) ? active car's track ? its track's coins ?
cars on that floor. Higher floors can occlude cars below. The active car draws
last when cars overlap on one floor. Coins with no track are omitted.

Prepared 700 px images load once. Each floor's static layers are combined and
reused while cars move; active-car, active-floor, and reservation changes rebuild
these stacks in a worker. The previous minimap stays visible until it finishes.
The completed transparent minimap is redrawn over the background each frame.
All surfaces use premultiplied alpha; reuse with `pygame.BLEND_PREMULTIPLIED`.

The read-only **Redraw time** field displays:

- **Minimap:** the most recent composition time in milliseconds, including any
  static-floor invalidation/rebuild. It excludes worker queue and UI polling time.
  This number stays unchanged when there is no new minimap state.
- **Preview:** the current background/minimap blit and display-flip time. This
  includes background loading/scaling when the selected background changes.

These measurements describe work time, not total input-to-display latency or
physical display refresh. Close either window to exit.

## Generated cache and benchmarks

Rebuild the cache after re-exporting artwork:

```powershell
.\.venv\Scripts\python.exe experiments/e12_pygame_minimap/benchmark.py --prepare-only
```

Source files under `asset/` are only read. Generated cache files live in `cache/`
and are ignored by Git. The application loads only 700 px images; native cropped
variants are retained for the separate benchmark tool.

```powershell
.\.venv\Scripts\python.exe experiments/e12_pygame_minimap/benchmark.py --samples 1200
```

The benchmark compares prepared-layer techniques separately from the app. Its
native reference now uses prepared native-resolution layers. `--prepare` also
refreshes comparison references. `--skip-video` omits the synthetic single-window
1080p YUV test, which excludes decoding, networking, and physical scanout.
Results under `results/` and `BENCHMARK_FINDINGS.md` from the earlier artwork are
historical and have not been remeasured for the latest export.
