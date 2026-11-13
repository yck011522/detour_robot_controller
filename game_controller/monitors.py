"""Read-only SDL/Windows monitor inventory. No cameras or robot connections."""

import ctypes as C
from datetime import datetime
import hashlib
import os
from pathlib import Path
import winreg

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

class Device(C.Structure):
    _fields_ = [("size", C.c_uint32), ("name", C.c_wchar * 32),
                ("description", C.c_wchar * 128), ("flags", C.c_uint32),
                ("device_id", C.c_wchar * 128), ("key", C.c_wchar * 128)]


class MonitorInfo(C.Structure):
    _fields_ = [("size", C.c_uint32), ("monitor", C.c_long * 4),
                ("work", C.c_long * 4), ("flags", C.c_uint32),
                ("device", C.c_wchar * 32)]


class Point(C.Structure):
    _fields_ = [("x", C.c_long), ("y", C.c_long)]


def edid_info(interface):
    """Read the registry EDID associated with this exact monitor interface."""
    parts = interface.split("#")
    if len(parts) < 4 or not parts[0].upper().endswith("DISPLAY"):
        return {"error": "No usable monitor interface path"}
    key = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{parts[1]}\{parts[2]}\Device Parameters"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            data, _ = winreg.QueryValueEx(handle, "EDID")
        if len(data) < 128 or data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
            return {"error": "Invalid EDID base block"}
        vendor = int.from_bytes(data[8:10], "big")
        result = {
            "manufacturer": "".join(chr(64 + ((vendor >> shift) & 31)) for shift in (10, 5, 0)),
            "product_code": f"{int.from_bytes(data[10:12], 'little'):04X}",
            "numeric_serial": int.from_bytes(data[12:16], "little"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "base_checksum_valid": sum(data[:128]) % 256 == 0,
        }
        for offset in (54, 72, 90, 108):
            block = data[offset:offset + 18]
            if block[:3] == b"\x00\x00\x00" and block[3] in (0xFC, 0xFF):
                field = "model" if block[3] == 0xFC else "text_serial"
                result[field] = block[5:18].decode("ascii", errors="replace").strip("\x00 \r\n")
        return result
    except OSError as error:
        return {"error": str(error)}


def collect():
    user32 = C.WinDLL("user32", use_last_error=True)
    # Establish physical pixel coordinates before initializing SDL.
    user32.SetProcessDPIAware()
    user32.MonitorFromPoint.argtypes = [Point, C.c_uint32]
    user32.MonitorFromPoint.restype = C.c_void_p
    user32.GetMonitorInfoW.argtypes = [C.c_void_p, C.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = C.c_int
    user32.EnumDisplayDevicesW.argtypes = [C.c_wchar_p, C.c_uint32, C.POINTER(Device), C.c_uint32]
    user32.EnumDisplayDevicesW.restype = C.c_int
    was_initialized = pygame.display.get_init()
    pygame.display.init()
    try:
        sdl = C.CDLL(str(Path(pygame.__file__).parent / "SDL2.dll"))
        sdl.SDL_GetDisplayBounds.argtypes = [C.c_int, C.POINTER(C.c_int)]
        sdl.SDL_GetDisplayBounds.restype = C.c_int
        rows = []
        for index, size in enumerate(pygame.display.get_desktop_sizes()):
            bounds = (C.c_int * 4)()
            if sdl.SDL_GetDisplayBounds(index, bounds) != 0:
                raise RuntimeError(f"Cannot read SDL display {index} bounds")
            x, y, width, height = bounds
            monitor = user32.MonitorFromPoint(Point(x + width // 2, y + height // 2), 0)
            info = MonitorInfo(size=C.sizeof(MonitorInfo))
            if not monitor or not user32.GetMonitorInfoW(monitor, C.byref(info)):
                raise C.WinError(C.get_last_error())
            row = {"sdl_index": index, "desktop_size": list(size), "bounds": list(bounds),
                   "windows_device": info.device, "primary": bool(info.flags & 1),
                   "monitors": []}
            adapter_index = 0
            while True:
                adapter = Device(size=C.sizeof(Device))
                if not user32.EnumDisplayDevicesW(None, adapter_index, C.byref(adapter), 0):
                    break
                if adapter.name.casefold() == info.device.casefold():
                    row["gpu"] = adapter.description
                    break
                adapter_index += 1
            child_index = 0
            while True:
                child = Device(size=C.sizeof(Device))
                if not user32.EnumDisplayDevicesW(info.device, child_index, C.byref(child), 1):
                    break
                row["monitors"].append({"name": child.description, "flags": child.flags,
                                        "interface": child.device_id, "edid": edid_info(child.device_id)})
                child_index += 1
            rows.append(row)
        return {"captured_at": datetime.now().astimezone().isoformat(),
                "pygame": pygame.version.ver, "sdl": list(pygame.get_sdl_version()), "displays": rows}
    finally:
        if not was_initialized:
            pygame.display.quit()


def resolve_serials(serials, report=None):
    """Resolve physical text serials to current SDL indexes; never guess."""
    if any(not serial.strip() for serial in serials):
        raise ValueError("Monitor text serials must not be empty")
    normalized = [serial.strip().casefold() for serial in serials]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Configured monitor text serials must be unique")
    report = collect() if report is None else report
    matches = {}
    for row in report["displays"]:
        active = [m for m in row["monitors"] if m["flags"] & 1]
        if len(active) != 1:
            continue
        edid = active[0]["edid"]
        serial = edid.get("text_serial", "").strip().casefold()
        if serial and edid.get("base_checksum_valid"):
            matches.setdefault(serial, []).append(row["sdl_index"])
    result = []
    for serial, key in zip(serials, normalized):
        indexes = matches.get(key, [])
        if len(indexes) != 1:
            raise RuntimeError(f"Monitor {serial!r}: expected one connected match, found {len(indexes)}. Run Calibrate Monitors.bat if needed.")
        result.append(indexes[0])
    return result
