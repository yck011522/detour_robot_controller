# E12 single-instance rendering findings

Historical measurements: these results use the previous artwork and the former
600/700 px options. The current preview is 700 px only; its cache has been
regenerated from the re-exported artwork, with floor opacity applied at startup.
These timings and comparison images have not been remeasured for that export.

Measured November 13, 2026, on this Windows machine: Core i9-14900HX, about 32 GiB RAM, NVIDIA RTX 4080 Laptop GPU plus Intel UHD Graphics. SDL selected the `direct3d` accelerated backend; this experiment does not identify the selected adapter. Pygame 2.6.1 / SDL 2.28.4. Background desktop activity and power state were not controlled.

## Incremental composition results

Times are milliseconds: **median / p95**, for one minimap instance. The original native renderer took 16,859?29,365 ms across four sequential active-car selections (median 24,424 ms). This is not a four-display benchmark. The original baseline has only four samples and no reliable p95 estimate.

| Technique | 600 pixels | 700 pixels |
|---|---:|---:|
| Prepared native images, cropped and opacity baked | 266.50 / 276.86 | 267.03 / 269.65 |
| Compose individual prepared-size layers | 8.04 / 9.70 | 10.07 / 11.85 |
| Additionally combine coin layers per track/floor | 6.27 / 7.40 | 8.31 / 9.61 |
| Additionally cache static floor stacks | 1.63 / 2.05 | 2.14 / 2.56 |

The first preparation step removes disk decoding, precomputes opacity/premultiplication, and crops transparent margins together. Their individual contributions are not separately isolated. Subsequent rows add one technique at a time. Native cropping preserves the original canvas alignment and produces pixel-identical reference output in the tested scenes.

Native cached rendering uses 12 timed samples. Other composition techniques use 1,200 samples after warming 24 combinations of active car, car positions, and reservation state. Positions include another car's track and a tag without a track. Floor-stack hits are steady-state measurements; a static configuration change requires rebuilding layers.

- Cold static-floor rebuild at 600 pixels: 11.24 ms median across 24 samples.
- Cold static-floor rebuild at 700 pixels: 13.44 ms median across 24 samples.

The fast preview rebuilds static stacks in the worker on active-car, active-floor, reservation, or size changes, retaining the previous completed minimap during the rebuild. It reuses stacks when a car moves within the same active floor. It keeps only the current static configuration instead of retaining every combination tested by the harness.

## Single-window synthetic video measurements

One visible 1920 ? 1080 accelerated SDL window. A full YUV420 frame is uploaded every frame, followed by the minimap overlay. Four prepared luma values alternate, with constant chroma. This exercises changing-frame uploads and rendering but is not representative video content or a codec benchmark.

| Size | Upload overlay every frame, median ms | Retain overlay texture, median ms | Paced composition + upload + draw: median / p95 / max ms | Missed deadlines |
|---|---:|---:|---:|---:|
| 600 | 2.05 | 0.66 | 4.20 / 6.02 / 10.08 | 0 / 600 |
| 700 | 2.55 | 0.81 | 4.72 / 7.38 / 12.17 | 0 / 600 |

The first two columns are unpaced CPU wall times through SDL_RenderPresent, include the YUV upload, and exclude composition. They are not physical scanout timings. The paced test changes the active car's tag and composes/uploads a new minimap on every frame, scheduled at 60 Hz for 600 frames (about ten seconds) per size. A missed deadline means the present call returned after the next scheduled frame boundary. Tag changes stay on floor G, so these are warm static-floor-cache tests.

Both sizes met all scheduled deadlines in this short synthetic test. This establishes useful single-instance rendering headroom, not production certification for camera decoding, network delivery, display scanout, or four simultaneous instances. A live-camera acceptance run remains necessary. No camera, robot, or main-controller configuration was accessed or changed.

## Visual compromise

For active cars 1 and 4 at each size, the native cached result is pixel-identical to the original full native composition. Preparing layers at target size changes edge pixels where translucent shapes overlap; combining coin/floor stacks adds small integer-rounding differences. Cars remain in the correct floor order.

Comparison files show **reference | fast candidate | absolute RGB error amplified 8x** over bg1.jpg. The normal images are very similar on inspection; the error panel reveals small outline/intersection differences. Metrics below describe these scenes and this background only. Global averages include transparent/background-only areas and should not be read as a bound on local edge error.

- 600 pixels, car 1: mean absolute RGB error 0.0457 levels out of 255; maximum channel error 23; 0.0197% of pixels differ by more than 8 in at least one channel. [Comparison](results/comparison_floor_cached_car1_600.png).
- 600 pixels, car 4: mean absolute RGB error 0.0561 levels out of 255; maximum channel error 23; 0.0067% of pixels differ by more than 8 in at least one channel. [Comparison](results/comparison_floor_cached_car4_600.png).
- 700 pixels, car 1: mean absolute RGB error 0.0432 levels out of 255; maximum channel error 28; 0.0147% of pixels differ by more than 8 in at least one channel. [Comparison](results/comparison_floor_cached_car1_700.png).
- 700 pixels, car 4: mean absolute RGB error 0.0543 levels out of 255; maximum channel error 27; 0.0055% of pixels differ by more than 8 in at least one channel. [Comparison](results/comparison_floor_cached_car4_700.png).

## Cache and integration

Preparation took 144.71 seconds. Generated cache data occupies about 9.38 MiB on disk. Cropped reduced layers decode to about 8.60 MiB at 600 and 11.66 MiB at 700; cropped native reference layers decode to 374.22 MiB. The fast preview loads both reduced sizes but not native layers. Its nine current static-floor surfaces require another 12.36 MiB at 600 or 16.82 MiB at 700, plus small combined coin layers and transient composition buffers. These are surface-storage figures, not total process RAM.

All 650 original asset-file hashes were checked unchanged after the experiment. Generated files are confined to this experiment's cache/results directories. The original native renderer remains available.

Recommended integration: prepare target-size sprites, retain per-floor static stacks, recompose only on state changes, and retain the final GPU overlay texture until it changes. Keep the changing video in its own YUV texture. The current production VideoWindow uploads overlays every draw and uses ordinary alpha; cached surfaces here are premultiplied, so integration must use the corresponding custom SDL blend mode or convert alpha representation. Production code was not modified.

[SDL custom blend mode documentation](https://wiki.libsdl.org/SDL2/SDL_ComposeCustomBlendMode).

See [README](README.md) for repeatable benchmark and cached-preview commands, and [raw results](results/benchmark.json).
