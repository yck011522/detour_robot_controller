# Detour robot controller

## Python environment setup (Windows PowerShell)

Install 64-bit Python 3.12 on each computer. The development environment was
created with Python 3.12.10 and verified with ur_rtde 1.6.5 and pygame 2.6.1.
Python is installed separately; the virtual environment holds project packages.

From the project root, create the environment and install the dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

These commands use the environment's interpreter directly, so activation is not
required. Select `.venv\Scripts\python.exe` as the Python interpreter in your IDE.
For an interactive terminal, optional activation is:

```powershell
.\.venv\Scripts\Activate.ps1
# Leave the activated environment when finished.
deactivate
```

Verify the installation without connecting to any robot or joystick:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import rtde_control, rtde_receive, rtde_io, pygame; from importlib.metadata import version; print('ur_rtde:', version('ur_rtde')); print('pygame:', version('pygame'))"
```

On the main control computer, install the same Python version, clone or copy this
project, and repeat the setup commands. Recreate `.venv` on each computer instead
of copying it. The environment is already excluded from Git by `.gitignore`;
`requirements.txt` records the project dependencies, including the pinned
ur_rtde version.

The package name is `ur_rtde`; its Python modules include `rtde_control`,
`rtde_receive`, and `rtde_io`. The robotic arm address is configured in
`config/address.ini` under `[robotic_arm]` as `host = 192.168.0.10`.
Installation and import verification do not establish a robot connection.

Library documentation: [ur_rtde](https://sdurobotics.gitlab.io/ur_rtde/).

## DIY gamepad input probe

Python 3.12 or another Python version supported by pygame:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python experiments/e1_gamepad_probe.py --list
.\.venv\Scripts\python experiments/e1_gamepad_probe.py --index 0 --hz 100 --seconds 30
```

Hold each button for at least one second to see its position in the once-per-second
console snapshot. B0 is the leftmost digit; masks use bit 0 for B0 through bit 11
for B11. Browser and SDL device/button numbering should be checked physically.
Replace `send_to_devices(mask)` with your device transport. It is currently a no-op;
the script does not communicate with any downstream device.

## Input strategy

Pygame's joystick API works with generic controllers without requiring an Xbox
mapping. SDL maintains input state; `pygame.event.get()` pumps pending OS input
and drains lifecycle events, then 12 `get_button()` calls read the current state.
Axis and button events are blocked because this probe wants snapshots. All SDL
calls stay on the main thread. Disconnect stops the loop; reconnect and restart.
Background input is requested through SDL, but verify it on your target system.

An event-based alternative processes JOYBUTTONDOWN/JOYBUTTONUP into a cached mask.
That preserves queued short press/release transitions, but still needs regular
event pumping. A current-state snapshot can miss a tap entirely between ticks;
if those taps must trigger actions, retain transitions separately. Even with an
event cache, sending only the final mask loses a press released before the send.

## Timing and limits

The acquisition metric includes the event pump, lifecycle handling, button reads,
and mask packing. It excludes the send hook, sleep, and logging. The second metric
includes the send hook (currently empty). Percentiles, maxima, count of reads at
or above 500 microseconds, tick intervals, wake lateness, and skipped ticks are
reported on completion. Samples are kept in memory; use bounded-duration runs.

100 Hz means a 10 ms period. A read under 0.5 ms does not mean physical press latency
under 0.5 ms: board scanning, debounce, USB reports, OS scheduling, and the phase of
the 100 Hz loop all matter. This script measures software cost, not USB report rate
or physical latency. Windows/Python scheduling offers no hard real-time guarantee.
Deadlines are absolute and missed ticks are skipped instead of sending a burst.
Do not put an unbounded serial/network operation in the send hook. A background
transport may need a bounded latest-state queue and its own safety policy.

Start with this benchmark on the target machine under realistic load. If input
processing exceeds the budget, compare a dedicated input worker with a cached
snapshot or a native HID reader after examining the board's HID report descriptor.
A blocking HID read must not run in the 100 Hz control loop. Faster software cannot
make the board produce USB reports faster.

`python experiments/e1_gamepad_probe.py --simulate --seconds 2` checks loop execution without
pygame or hardware; it is explicitly not a controller performance benchmark.

References: [Pygame joystick](https://www.pygame.org/docs/ref/joystick.html),
[Pygame events](https://www.pygame.org/docs/ref/event.html),
[SDL event pump](https://wiki.libsdl.org/SDL2/SDL_PumpEvents).

## E2: minimal 100 Hz controller

```powershell
.\.venv\Scripts\python experiments/e2_gamepad_controller.py --index 0
```

E2 continuously polls the first 12 buttons at a target rate of 100 Hz and prints
`B0..B11=000000000000` every tenth read (about ten prints per second). B0 is the
leftmost digit; 1 means pressed and 0 means released. Press Ctrl+C to stop.

There are no performance measurements, statistics, game rules, or device output.
The clock is used only to schedule the 10 ms loop. Python/Windows scheduling is
not hard real time. If work overruns, the loop resumes after a fresh period rather
than doing catch-up reads. A `connected` boolean tracks joystick availability.
The loop keeps running with all-zero buttons while disconnected and retries the
selected controller index each tick, including when started without a controller.
Reconnection resumes button reads automatically. With multiple controllers,
indexes may change after unplugging; `--index` selects the current index, not a
permanent device identity. Controllers must expose at least 12 buttons.
Every function includes explanatory comments. E1 remains the timing probe.
