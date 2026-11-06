# E8: fullscreen IP camera test

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe experiments/e8_pygame_video_stream/video_stream.py
```

Opens a 1920x1080 fullscreen window using car 1's camera and display settings
from `config/address.ini`: `[car_1_camera] rtsp_url` and
`[car_1_display] display_index`. Press **Escape** or **Q** to quit.
Pygame is already in the root requirements; `ffmpeg.exe` must also be on PATH
(the FFmpeg installation used for ffplay normally includes it).

```powershell
# Inspect the screens without opening a fullscreen window.
.\.venv\Scripts\python.exe experiments/e8_pygame_video_stream/video_stream.py --list-displays

# Select another car; its screen is selected automatically from address.ini.
.\.venv\Scripts\python.exe experiments/e8_pygame_video_stream/video_stream.py --car 2
```

The configured left-to-right mapping for cars 1 through 4 is SDL displays
**2, 3, 0, 1**. Display indexes start at zero. Update the corresponding
`[car_N_display] display_index` if screen arrangement changes. Spare cars 5 and 6
need display sections added before use. Missing, invalid, or unavailable display
settings produce an error instead of falling back to another screen.
Run each of cars 1 through 4 using `--car`. This test shows one camera at a time.
Video is scaled to fill 1920x1080, including stretching non-16:9 input.

FFmpeg decodes RTSP over UDP with the tested `nobuffer`, `low_delay`, tiny probe,
and zero analysis duration. RTP packet reordering is disabled to reduce delay,
at the cost of more visible corruption/loss on an unreliable network. A worker
continuously drains RGB frames into a single newest-frame slot. The Pygame loop
discards stale frames, requests no vsync, and does not pace playback by stream
timestamps; ffplay's `-sync ext`, `-framedrop`, and `setpts=0` therefore are not
copied as FFmpeg output options. Audio is disabled.

This minimizes application buffering; it does not guarantee a latency value.
Camera encoding, keyframe interval, networking, decoding, and display scanout
still contribute. Aggressive probing may fail on some streams, and startup may
wait for a keyframe. If FFmpeg cannot detect the stream, try increasing
`-probesize` to `32768` and `-analyzeduration` to `1000000` in the script.
Connection errors are printed in the terminal; Escape remains available while
waiting for the camera. An ended stream exits; restart manually to reconnect.

References: [Pygame display API](https://www.pygame.org/docs/ref/display.html),
[FFmpeg RTSP options](https://ffmpeg.org/ffmpeg-protocols.html#rtsp).
