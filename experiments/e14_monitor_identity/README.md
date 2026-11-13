# E14: monitor identity baseline

## Interactive calibration

Close the game controller, then double-click `Calibrate Monitors.bat` in the project
root, or run from the project root:

```powershell
.\.venv\Scripts\python.exe experiments/e14_monitor_identity/calibrate.py
```

Four fullscreen Pygame windows show A, B, C, D. Click any label window if needed
to focus it, then type the four letters in **car 1 through car 4 order**. For
example, `DCAB` assigns D to car 1, C to car 2, A to car 3, and B to car 4.
The assignments appear on all screens as you type. Backspace edits; Escape or
closing a window cancels without writing. Enter saves once each letter is used.

The script adds/updates `monitor_text_serial` in each `[car_N_display]` section
of `config/address.ini`. It preserves comments, other settings, and removes obsolete
`display_index` values. A timestamped backup is saved under
`logs/e14_monitor_identity/`. Controller startup resolves these text serials to current SDL indexes,
rejecting missing or ambiguous matches.

Calibration rejects missing/duplicate serials, invalid EDID, anything other than
four displays, and display mapping changes detected before saving. `--check`
validates identities without showing windows or writing configuration.

Verification: identity check passed on the four connected monitors; four-window
creation and Escape cancellation passed. Config update, repeat update, backup,
newline preservation, and preservation of unrelated values were checked against
a temporary copy. Physical label placement and the desired assignment require
the operator's visual check.

## Baseline capture

Run from the project root:

```powershell
.\.venv\Scripts\python.exe experiments/e14_monitor_identity/baseline.py
```

Prints JSON and a compact summary, and saves a timestamped JSON snapshot under
`logs/e14_monitor_identity/`. Does not open visible windows, connect to hardware
controllers/cameras, or change display settings or controller configuration.
Requires Windows and the existing project virtual environment. Registry read
failures appear as errors in the relevant monitor's EDID record.

The script maps SDL bounds to Windows monitors, enumerates their monitor device
interfaces, and reads their associated cached registry EDID. Coordinates use
physical pixels. Configured cars are taken from the current `address.ini`;
these are configured assignments, not visually verified physical assignments.

## First capture: 2026-11-13

All four monitors report model LEN T24i-20 and valid base EDID checksums.

| SDL index | Configured car | Desktop x | GPU | EDID text serial |
| --- | --- | --- | --- | --- |
| 0 | 3 | 0 (primary) | Intel UHD Graphics 630 | VKHA0785 |
| 1 | 2 | 1920 | Intel UHD Graphics 630 | VKHA0784 |
| 2 | 4 | -1920 | NVIDIA GeForce GT 730 | VKHA0314 |
| 3 | 1 | -3840 | NVIDIA GeForce GT 730 | VKHA0790 |

The numeric serial is 16843009 on every monitor: it cannot distinguish them.
Text serials and full EDID hashes are distinct in this capture. A single capture
does not establish stability across boots.

## Next experiment

1. Keep cables and Windows display layout unchanged. Confirm the configured car
   assignments above correspond to the desired physical monitors.
2. Reboot normally, then run the same command again.
3. Compare text serials, SDL indexes, Windows device names/interfaces, and bounds
   with the first saved snapshot. Repeat for another reboot if necessary.
4. If the text serials remain stable and unique, implement startup resolution of
   manufacturer/model/text serial to the current SDL index. Reject missing or
   ambiguous matches instead of assigning another screen.

References:
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaydevicesw
- https://learn.microsoft.com/en-us/windows-hardware/drivers/display/overriding-monitor-edids
