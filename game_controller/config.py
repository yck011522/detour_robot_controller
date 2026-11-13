"""Read device identity separately from changeable M1 behavior settings."""

import configparser
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(address_path=None, game_path=None):
    address = configparser.ConfigParser(interpolation=None)
    game = configparser.ConfigParser(interpolation=None)
    for parser, path in ((address, address_path or ROOT / "config/address.ini"),
                         (game, game_path or ROOT / "config/game_config.ini")):
        with Path(path).open(encoding="utf-8") as source:
            parser.read_file(source)
    settings = {key: game.getfloat("timing", key) for key in game["timing"]}
    if any(not math.isfinite(value) or value <= 0 for value in settings.values()):
        raise ValueError("Timing settings must be finite and positive")
    if settings["control_hz"] != 50:
        raise ValueError("The agreed M1 control cadence is 50 Hz")
    if settings["display_fps"] not in (30, 60):
        raise ValueError("display_fps must be 30 or 60")
    if settings["ui_snapshot_hz"] > settings["control_hz"]:
        raise ValueError("UI snapshot rate cannot exceed control cadence")
    settings["display_fps"] = int(settings["display_fps"])
    present = {int(value.strip()) for value in game["m1"]["present_cars"].split(",") if value.strip()}
    if not present <= {1, 2, 3, 4}:
        raise ValueError("present_cars must contain car IDs 1-4")
    cameras, buttons, displays = [], [], set()
    for car in range(1, 5):
        display = address.get(f"car_{car}_display", "monitor_text_serial").strip()
        if not display or display.casefold() in displays:
            raise ValueError("Monitor text serials must be nonempty and unique")
        displays.add(display.casefold())
        url = address[f"car_{car}_camera"]["rtsp_url"].strip()
        if not url.startswith("rtsp://"):
            raise ValueError(f"Car {car}: invalid RTSP URL")
        cameras.append((car, url, display))
        mapping = tuple(address.getint(f"car_{car}_control", f"{key}_button") for key in ("forward", "left", "right"))
        if min(mapping) < 0:
            raise ValueError("Button indexes cannot be negative")
        buttons.append(mapping)
    keys = address["admin_keyboard_shotcut"]
    bindings = {
        "toggle": keys["admin_toggle_button"], "quit": keys["admin_quit_button"],
        "cancel": keys["admin_cancel_button"],
        "accept": keys["admin_accept_button"],
        "forward": keys["admin_car_control_forward_button"],
        "left": keys["admin_car_control_left_button"], "right": keys["admin_car_control_right_button"],
        "backward": keys["admin_car_control_backward_button"], "manual": keys["admin_manual_control_modifier_button"],
        **{f"car_{car}": keys[f"admin_select_car_{car}_button"] for car in range(1, 5)},
    }
    bindings = {key: value.strip().strip("\"'") for key, value in bindings.items()}
    return {"settings": settings, "port": address["telemetry_modem"]["serial_port"],
            "cameras": cameras, "buttons": buttons, "bindings": bindings, "present": present,
            "admin": game.getboolean("m1", "start_in_admin"),
            "log_directory": ROOT / game["logging"]["directory"]}
