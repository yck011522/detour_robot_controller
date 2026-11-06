# E9: minimal four-camera Pygame prototype

This folder contains just `four_streams.py` and this README. All four cameras
are now configured for **H.264**, following the user's change to car 1 and
successful playback check. The prototype expects H.264 and reports an error if
another codec is selected in a camera. E8 remains independent.

## Run

From the repository root in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe experiments/e9_pygame_4streams/four_streams.py

# Higher display rate; keep the conservative default at 30 fps.
.\.venv\Scripts\python.exe experiments/e9_pygame_4streams/four_streams.py --fps 60

# Optional animated graphics, or a timed smoke check that closes automatically.
.\.venv\Scripts\python.exe experiments/e9_pygame_4streams/four_streams.py --fps 60 --overlay
.\.venv\Scripts\python.exe experiments/e9_pygame_4streams/four_streams.py --fps 60 --overlay --seconds 20
```

Press **Escape/Q** in any focused video window or **Ctrl+C** in the terminal to
stop all four. A closed or failed stream stops the group. Startup can wait for a
keyframe. Socket open/read timeouts are five/three seconds. There is no automatic
reconnection. The timed-run duration includes player-process startup.

Needs Windows, the root requirements (Pygame 2 and PyAV 18), and Pygame's bundled
`SDL2.dll`. No separate FFmpeg executable, NumPy or hardware-decoder setup is
needed. This is the tested Intel/NVIDIA desktop configuration, not a general
multi-platform media player.

## Camera and screen configuration

The script reads `config/address.ini` relative to its own location, so it also
works when launched from another working directory. Screen indexes are SDL
indexes, not Windows Settings' identification numbers.

| Car | URL section/key | Display section/key | Current SDL display |
| --- | --- | --- | --- |
| 1 | car_1_camera / rtsp_url | car_1_display / display_index | 2 |
| 2 | car_2_camera / rtsp_url | car_2_display / display_index | 3 |
| 3 | car_3_camera / rtsp_url | car_3_display / display_index | 0 |
| 4 | car_4_camera / rtsp_url | car_4_display / display_index | 1 |

Each window uses desktop fullscreen with a 1920x1080 logical drawing area.
These four desktops are 1920x1080. SDL scales video to fit the logical area;
desktops with another aspect ratio may have letterboxing. Display indexes must
be unique and connected. Each player prints its screen, GPU name and selected
renderer. GPU ownership is resolved from Windows monitor/device information,
not hardcoded by car number. The tested selection is D3D9 for NVIDIA screens,
D3D11 for Intel screens; the exact renderer is requested without silent fallback.

## Retained architecture

```text
Camera --RTSP/TCP--> PyAV/FFmpeg CPU decoder --newest YUV frame--> GPU texture
                                                                + overlay
                                                                --> screen
```

One Python process owns each window. A decoder thread owns and closes its PyAV
container. SDL events and rendering stay on that process's main thread. Two
frame-decoding threads provide throughput at the cost of about one frame of
extra decoding delay. A one-frame deque replaces stale decoded frames while
preserving every compressed reference frame needed for decoding. At a 30 fps
display target, the cameras' 60 fps streams are still decoded at their source
rate; old display frames are intentionally replaced.

The GPU converts planar YUV to RGB, scales it, and composes the final image.
Decoded video lives in system RAM before texture upload. The small native SDL
bridge is retained because Pygame's Surface/Texture APIs do not directly expose
the planar-YUV upload path we tested. It uses PyAV's plane pointers and actual
row pitches, avoiding video RGB conversion and packing copies in Python.

Every five seconds the console reports decoded/displayed fps and corrupt frames
or packets. No log files, JSON reports or screenshots are generated. CPU/GPU
comparisons, codec/transport switches, adapter inspection tools, test files and
old capture folders were removed after their results were recorded below.

## Future Pygame overlays

Edit `draw_overlay()` in `four_streams.py`. With `--overlay`, it draws into a
640x128 transparent Pygame Surface at the top left. Ordinary `pygame.draw`, font
rendering and Surface drawing happen on the CPU; the RGBA panel is then uploaded
and blended by the GPU. Keep this panel's size fixed, or recreate its texture
when changing the size. Prefer small panels over uploading a mostly transparent
full-HD image every frame. Graphics currently update with displayed video
frames and stop animating if video stalls.

## Lessons learned

1. **One smooth camera did not prove four-camera capacity.** Four 1080p60 feeds
   require 240 decoded HD frames per second. The initial application used about
   88-90% total PC CPU according to Task Manager. RAM rose from about 12.1 GB to
   12.5 GB, so the observed problem was primarily CPU/throughput, not a large
   application memory increase.
2. **PyAV helped by changing the data path.** It uses FFmpeg libraries directly;
   it is not a faster codec implementation. The old path converted video to RGB,
   packaged raw frames into OS pipes and performed full-screen software blits.
   Direct frame access, native YUV uploads and GPU composition removed much of
   that conversion, transfer and blitting work. Gains came from the combination.
3. **Minimum buffering is not the same as minimum usable latency.** UDP with
   packet reordering disabled produced missing-reference errors and green
   corruption. TCP gave clean results. Removing `nobuffer` also preserved initial
   HEVC reference packets during the historical tests. TCP can stall on loss;
   there is no universal transport winner for every network.
4. **Frame pacing and bounded queues matter.** Always display the latest frame,
   skip stale display frames and avoid an ever-growing playback queue. A 30 fps
   target gives overlay headroom. A 60 fps target was substantially improved by
   fixing renderer selection but never established a guaranteed 60 fps lock.
5. **Window location did not select the D3D11 GPU.** Pygame's bundled SDL 2.28.4
   used Intel adapter 0 for all D3D11 renderers. Rendering on the connected GPUs
   helped. Adapter ownership was verified during testing through native device
   queries; merely logging 'Direct3D 11' would not have proved that.
6. **Hardware decoding was not automatically better.** Intel and NVIDIA decode
   APIs worked, but their RAM downloads, uploads, driver costs and format
   conversions made the tested pipeline slower. Even decode/render on the same
   physical GPU still used a RAM round trip. No fully GPU-resident, zero-copy
   pipeline was implemented, so these tests do not establish intrinsic GPU
   decoder limits.
7. **H.264 is the chosen baseline.** It fits the successful CPU-decoded playback
   path. H.265 can save bandwidth/storage; that was not the priority here. The
   old tests used different camera scenes and were not a controlled codec A/B
   test. All cameras are now H.264, but historical figures below were measured
   before car 1 was changed.
8. **Cleanup and measurements need care.** Containers must close on their owning
   decoder threads. Bounded network waits keep stopping responsive. The old
   diagnostic multiprocessing queue once deadlocked when parents joined before
   draining reports; removing that reporting queue removes the failure mode.
   Screenshot readback disrupted timing, so benchmark comparisons omitted it.

## Historical measurements, November 6, 2026

These measurements are preserved from the earlier multi-file implementation.
At that time car 1 was **HEVC**, and cars 2-4 were **H.264**, all 1080p60. They
are not new benchmarks of today's all-H.264 configuration. The user has confirmed
that all-H.264 playback works. CPU percentages below refer to player processes
normalized to the whole PC, excluding other programs and driver work outside
those processes; compare cautiously with the initial total Task Manager figure.

The initial optimized 35-second TCP/software-decoding run, with overlays and a
30 fps target, reported **14.8% combined player CPU** and zero corrupt frames.
After the first five-second startup interval:

| Car | Displayed fps across intervals | Mean render time |
| --- | --- | --- |
| 1 | 29.9-30.0 | 2.7 ms |
| 2 | 29.8-30.0 | 4.2 ms |
| 3 | 29.5-30.0 | 4.0 ms |
| 4 | 29.6-30.0 | 3.9 ms |

For the GPU comparisons below, each configuration ran **sequentially for 40
seconds**, with four videos and identical animated overlays. The first ten
seconds were excluded; table values average the next five five-second intervals
per car. Small differences between single trials are not conclusive. The best
60 fps configuration was repeated once.

## Verified hardware and wiring

WMI, NVIDIA's driver query and DXGI identify **NVIDIA GeForce GT 730
(10de:1287), not GTX 970**, driver 475.14. Intel UHD 630 (8086:3e92), driver
31.0.101.2114; i7-8700, 6 cores / 12 logical processors.

| Car | SDL display | Windows output | Attached GPU |
| --- | --- | --- | --- |
| 1 | 2 | DISPLAY1 | NVIDIA GT 730 |
| 2 | 3 | DISPLAY2 | NVIDIA GT 730 |
| 3 | 0 | DISPLAY6 | Intel UHD 630 |
| 4 | 1 | DISPLAY5 | Intel UHD 630 |

Monitor model names repeat, so SDL desktop rectangles were matched to DXGI
outputs. Actual D3D11 render devices were queried through
`SDL_RenderGetD3D11Device -> IDXGIDevice::GetAdapter -> IDXGIAdapter::GetDesc`.
**All four original D3D11 renderers use Intel**, including NVIDIA-connected
screens. SDL 2.28.4 explicitly selects adapter 0 for this backend.

The retained rendering path uses D3D9 on NVIDIA screens and D3D11 on Intel screens.
D3D9's creation adapter was queried too: cars 1-2 really rendered on NVIDIA,
cars 3-4 on Intel. This changes backend and adapter together, so it does not
isolate adapter choice alone. D3D9 also needs hardware-decoded NV12 converted to
planar YUV; CPU-decoded YUV needs no such layout conversion.

## Results at a 30 fps target

| Decode | Render | Car 1 fps | Car 2 fps | Car 3 fps | Car 4 fps | Player CPU |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| CPU, all | Intel D3D11, all | 30.00 | 29.91 | 30.00 | 30.00 | 12.37% |
| Intel, all | Intel D3D11, all | 29.09 | 28.91 | 29.11 | 28.99 | 17.81% |
| NVIDIA, all | Intel D3D11, all | 29.81 | 27.19 | 27.58 | 27.67 | 32.49% |
| NVIDIA 1-2, Intel 3-4 | Intel D3D11, all | 29.33 | 27.23 | 28.79 | 28.59 | 28.00% |
| CPU, all | Screen's GPU | 30.00 | 29.97 | 29.98 | 29.99 | 11.00% |
| Screen's GPU | Screen's GPU | 27.73 | 22.62 | 29.41 | 29.57 | 32.67% |

All cases completed normally with zero corrupt frames/packets. Per-car p95
presentation intervals were about 35-37 ms with CPU decoding, versus 48-82 ms
across GPU-decoded cases. A 30 fps period is 33.33 ms.

## Results at a 60 fps target

Both cases used CPU decoding, TCP and identical animated overlays:

| Render | Car 1 fps | Car 2 fps | Car 3 fps | Car 4 fps | Player CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| Intel D3D11, all | 56.17 | 57.96 | 53.47 | 53.77 | 43.37% |
| Screen's GPU | 58.83 | 59.01 | 58.07 | 58.19 | 17.84% |
| Screen's GPU, repeat | 58.61 | 59.00 | 57.59 | 58.58 | 16.83% |

All three completed normally with zero corrupt frames/packets. Screen-attached
rendering's per-car p95 presentation intervals were 21-30 ms. This improves
throughput and CPU cost but is not locked 60 fps.

## Conclusions and limits

CPU decode plus screen-attached render is now the default. The conservative
display target remains 30 fps; `--fps 60` enables the higher-rate option.
Matching the **render** adapter helped, but matching the **decode** adapter did
not improve this pipeline. Hardware frames still download to system RAM and
upload again, even on the same physical GPU. A fully GPU-resident decoder-to-
texture pipeline was not implemented or tested; these results do not establish
the GPUs' intrinsic decoding limits.

The selected NVIDIA D3D11 decode paths returned NV12 for both codecs. An explicit
FFmpeg adapter-1 check logged the GT 730 PCI identity and decoded an HEVC frame.
That confirms an operational API path, not how much work uses fixed-function
hardware versus GPU shaders or CPU driver code.

## Measurement limits and references

Reported fps counts render/present submissions, not physical screen scanout.
SDL's non-vsync present may return while GPU work is pending. Post-decode age
measured during development excluded camera encoding, network delay, decoder
backlog and scanout. Physical camera-to-screen latency has not been measured.
The initial standalone capability probes included startup-buffer draining and
one CPU/HEVC timeout; their fps values were not used to rank configurations.
A preliminary baseline overlapping those probes was excluded as well.

The raw diagnostic files and benchmark utilities have been removed to keep this
a minimal prototype; the tables above retain the measured results. Historical
adapter queries verified both D3D11 and D3D9 devices, and hardware-decoder tests
used explicit DXGI adapter indexes without implicit software fallback. Before
consolidation, seven regression checks covered frame replacement, corrupt input,
decoder failures, physical adapter selection and report-queue shutdown. Native
checks exercised padded YUV planes, NV12 conversion, overlay colors and cleanup
on both GPUs.

After consolidation, a 20-second live check with **all four cameras on H.264**,
`--fps 60 --overlay`, completed and closed normally. The post-startup console
intervals showed approximately **56.8-59.8 displayed fps** and zero corrupt
frames/packets. CPU usage was not remeasured during this refactor. Hardware-free
checks also passed for newest-frame replacement, H.264 validation, decoder
failure/cleanup, the 2/3/0/1 screen mapping and duplicate-display rejection.

References:
[PyAV](https://pyav.basswood.io/docs/18.0/api/codec.html),
[Pygame SDL2 windows](https://www.pygame.org/docs/ref/sdl2_video.html),
[SDL YUV uploads](https://wiki.libsdl.org/SDL2/SDL_UpdateYUVTexture),
[SDL 2.28.4 D3D11 adapter selection](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/render/direct3d11/SDL_render_d3d11.c),
[SDL 2.28.4 D3D9 screen adapter selection](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/render/direct3d/SDL_render_d3d.c),
[FFmpeg RTSP options](https://ffmpeg.org/ffmpeg-protocols.html#rtsp).
