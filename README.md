# Detour robot controller

## Supervised robot movement and queue tests

The [E14 test guide](experiments/e14_robot_step_test/README.md) covers individual
actions, A-D pickup/place, directed home travel, and the three-plate queue CLI.
Movement settings are in `config/robot_movement.ini`. Preview commands do not
connect to hardware; live operation requires explicit operator confirmation.
These experiments are separate from the game controller and do not acquire
car-zone tokens. See the [2026-11-15 validation](experiments/e14_robot_step_test/VALIDATION_2026-11-15.md).
The game controller carries its own copy of the validated primitives in
`game_controller/robot/` and never imports from `experiments/`.

## Robot in the game controller

```powershell
# Real UR10e (default). Automatic arm startup; supervise movement.
.\.venv\Scripts\python.exe -m game_controller
# Boards never move; no robot or tool connection is opened.
.\.venv\Scripts\python.exe -m game_controller --robot off
# Simulated robot: same events and timing-free steps, no connections.
.\.venv\Scripts\python.exe -m game_controller --robot dry-run
```

The robot runs in its own process (`game_controller/robot/worker.py`) and keeps
moving the three boards between zones A-D. Startup order: automatic arm power-on
and brake release, home detection, then the floorboard questions. Transfers start
after board confirmation and leaving admin mode.

1. Startup requires Remote Control, NORMAL safety and no running program. It
   waits for stable IDLE before brake release, then stable RUNNING before reading
   position. At either home it continues. Otherwise
   the screens say so: **hold Left** to move to the AC home or **hold Right** to
   move to the BD home (you watch the path; releasing stops it and re-checks the
   position). **R** checks again, **Enter** runs the game without the robot,
   **Escape** quits. The same choices appear if the robot cannot be reached.
2. Confirm the floorboard locations. With the robot, exactly three boards are
   active (zone or gripper) and one is spare. Without the robot a fourth board may
   also sit in a zone, and no zone is ever closed by the robot.

Zone rules (`game_controller/zones.py`): Car 1 competes for A/B and Car 2 for
C/D; Cars 3/4 never hold tokens. The robot only claims a zone no car holds; the
closure is published in the topology byte and the robot waits for the zone's car
to echo a newer snapshot (absent or telemetry-silent cars owe nothing). A token is
released by a new tag with neither `inzone` nor `reservezone`. After 10 s without
forward input or 60 s total, a token holder is evacuated: the controller pushes
the stick forward toward the safe exit before the normal filter and branch lock,
so a locked choice stays locked. Evacuation and its timers do nothing in admin
mode.

Pickup order is least recently touched (`game_controller/scheduler.py`) and
never the plate just placed; if nothing else is eligible the robot waits
indefinitely. Entering admin mode pauses the robot at its next waypoint and
leaving resumes it. **Q** then **Enter** finishes the transfer in progress, puts
the robot home empty-handed and then quits (**Q** + **Enter** again quits
immediately, as does quitting while the robot is in a fault). Faults keep the
claimed zones closed and block further robot operations until restart.

On quit, the robot process sends Dashboard `stop` and `power off`, then verifies
POWER_OFF. This powers off the arm and engages its brakes; the controller stays
on. Cleanup failures are reported. Forced termination or lost connections can
prevent power-off.

Missing-board and robot-claimed zones show their existing zone artwork on every
minimap. Overlays update even when cars are stationary, and disappear once a
board is present and the robot releases its claim.

Robot logs and transcripts are written to `logs/robot/`. Joint speeds were
doubled to 10 and 20 deg/s in `config/robot_movement.ini`; that is not yet
validated on the robot. Protective-stop recovery is not implemented.

## Robot pose teaching

```powershell
.\.venv\Scripts\python.exe utility/robot_pose_teaching.py
```

Uses the robot host in `config/address.ini` and displays live joint positions
and TCP pose through RTDE receive only. Position the arm using its normal
controls, press **S** to capture, then type a pose name and press Enter to save
to `config/robot_pose.ini`. A blank name cancels; replacing an existing name
requires confirmation. Press **Q** or Ctrl+C to exit. Joint angles are radians;
TCP values are base-relative x/y/z in metres and rx/ry/rz rotation vector in
radians, using the robot's active TCP offset. The utility does not command motion.

## M1 controller and diagnostic UI

```powershell
.\.venv\Scripts\python.exe -m game_controller
```

Starts four camera/status views in admin mode. Keyboard controls the selected
car there; joystick controls players in normal mode, with no enable switch. Admin
controls are top overlays on those four screens; no fifth visible window.
Space toggles admin/player mode; Q opens Enter/Escape quit confirmation.
Read [the M1 test guide](docs/M1_TEST_GUIDE.md) before supervised bench
driving. Behavior settings are in `config/game_config.ini`; device addresses,
ports, button/key and display mappings remain in `config/address.ini`.
Monitors are selected by `monitor_text_serial`, resolved to the current SDL
index at startup. Missing or ambiguous matches stop startup. To assign the four
screens, close the controller and run `Calibrate Monitors.bat` in the project
root; type the displayed letters in car 1 through car 4 order and press Enter.
Without `--robot` the controller never connects to or commands the UR robot or
end effector.

The four player displays use the E13 HUD and minimap from
`game_controller/ui_layout.py` and `game_controller/minimap.py`. Press Space to
leave the initial admin view and see the player layout. Telemetry, raw input
indicators, and pause artwork use live controller state. Markers use registered
`nfc_uid` values in `asset/map_attributes.csv` (eight hexadecimal digits); replace
the CSV and restart to load new registrations. Unknown locations have no marker.
Not Present hides that car on every map and shows telemetry-loss artwork on its
own screen. NFC arrivals collect coins on the car's own track in both player and
admin mode: silver earns 1 point; tags with `video_id` are gold and earn 10.
Collected coins disappear for 120 seconds (2 minutes). After respawn, another collection
requires observing a different tag and returning; staying parked does not score.
Adjust `silver_points`, `gold_points`, and `respawn_seconds` under `[coins]` in
`config/game_config.ini`, then restart. Totals are saved in `config/coin_count.ini`;
all coins are available on startup, with the saved totals retained. Stop the
controller before editing totals.

Player AUTO steering uses the current NFC tag's `split` and `mergeedge` labels. At a
split, input can change the edge choice while NFC age is at most 100 ms. Above
100 ms the last selection locks until a different UID is observed; forward
still controls stop/resume. Adjust `[navigation] split_lock_age_ms` in
`config/game_config.ini`, then restart. Merges force the labeled edge; ordinary
tags clear turn bits. Admin AUTO and MANUAL use direct keyboard commands and
bypass tag-based steering, including the split lock and ordinary-tag centering.
Player AUTO permits normal split choices into zones with a board in the confirmed
startup layout; empty zones force `safeexit`. Branch locks remain authoritative.
Robot/car reservations are still deferred.
The split HUD shows the effective branch; `NAVIGATION` logs record edge and lock
changes. The operator confirmed intersection behavior and the admin bypass
work on the current setup. Broader map/car and measured timing coverage remain.
See the [milestone audit](development_plan/PROGRESS_AUDIT_2026-11-14.md) for status
and remaining work.
In `config/game_config.ini`, `[ui] video_wash_alpha`
controls the gray video wash (0?255, initially 0). Display pacing targets 60 FPS;
the independent control loop remains 50 Hz.


For an automated stop-only observation:

```powershell
.\.venv\Scripts\python.exe -m game_controller --stop-only --seconds 30
```

## USB NFC tag registration

The controller starts with board-location questions on all four displays.
Select P/Q/R/S with 1–4, cycle locations with Left/Right, and press Enter to
confirm. Driving stays stopped until the layout is saved. The arrangement is
recalled from `config/board_state.ini` but requires confirmation each launch.
See [board startup instructions](docs/BOARD_STARTUP_GUIDE.md).

Physical registrations resolve the CSV's `<a2>` etc. references using that
layout. Navigation, minimap and coins share the mapping. Known unused board
tags are ignored and preserve the preceding navigation/zone context.

Run the wired scanner utility and select its serial port from the list:

```powershell
.\.venv\Scripts\python.exe utility/NFC_tag_scanner.py
# Or select its known port directly:
.\.venv\Scripts\python.exe utility/NFC_tag_scanner.py --port COM12
```

Uses the existing wired scanner protocol at 115200 baud. The console displays
the latest UID, age and reader status. Press **S**, then type a name such as
`p1` and press Enter to save that captured UID. Continue scanning/naming tags;
press **Q** at the live display or Ctrl+C to quit. A blank name cancels saving.
Serial reading continues during naming, but the captured UID stays fixed.

Registrations are stored as `name = UID` in the `[tags]` section of
`config/dynamic_nfc.ini`, with eight uppercase hexadecimal digits. Existing
entries survive subsequent sessions. Replacing an existing name asks for
confirmation; assigning one UID to multiple names is rejected. Files are
replaced atomically after each save. Close the Rhino scanner before opening
the same port here. This utility registers physical tags; the controller's
startup layout determines their destination TIDs.

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
