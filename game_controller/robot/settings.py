"""Validated robot settings, taught poses, payloads and force-action completion."""

import configparser
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOVEMENT_CONFIG = ROOT / "config/robot_movement.ini"


def read_ini(path):
    config = configparser.ConfigParser(interpolation=None)
    with Path(path).open(encoding="utf-8") as source:
        config.read_file(source)
    return config


def load_poses(path=ROOT / "config/robot_pose.ini"):
    config = read_ini(path)
    poses = {}
    for name in config.sections():
        joints = [float(v) for v in config[name]["joint_positions"].split(",")]
        if len(joints) != 6 or not all(map(math.isfinite, joints)):
            raise ValueError(f"Invalid taught joints: {name}")
        poses[name] = joints
    return poses


def load_payload(name, path=ROOT / "config/game_config.ini"):
    if name not in ("tool_only", "tool_with_floor"):
        raise ValueError(f"Unknown payload: {name}")
    section = read_ini(path)["robot_payload_" + name]
    mass = section.getfloat("mass_kg")
    cog = [section.getfloat("cog_" + axis + "_m") for axis in "xyz"]
    if not math.isfinite(mass) or mass <= 0 or not all(map(math.isfinite, cog)):
        raise ValueError(f"Invalid payload: {name}")
    return mass, cog


def load_settings(path=MOVEMENT_CONFIG):
    config = read_ini(path)
    required = {
        "joint_motion": ("speed_deg_s", "acceleration_deg_s2", "arrival_tolerance_deg",
                         "stationary_velocity_rad_s", "settle_seconds", "step_dwell_seconds", "timeout_seconds"),
        "cross_home": ("speed_deg_s", "timeout_seconds"),
        "compliance": ("translation_speed_mm_s", "orientation_deviation_deg"),
        "tightening": ("downward_force_n", "max_downward_displacement_mm", "timeout_seconds"),
        "release": ("target_upward_displacement_mm", "min_accepted_upward_displacement_mm", "timeout_seconds"),
        "placement_release": ("upward_force_n",),
        "pickup_release": ("upward_force_n",),
        "runtime": ("receive_hz", "loop_sleep_seconds", "sample_interval_seconds",
                    "feedback_timeout_seconds", "tool_reply_timeout_seconds", "watchdog_min_hz",
                    "stop_joint_deceleration_rad_s2", "stop_linear_deceleration_m_s2",
                    "payload_mass_tolerance_kg", "payload_cog_tolerance_m"),
    }
    values = {}
    for section, keys in required.items():
        values[section] = {}
        for key in keys:
            value = config.getfloat(section, key)
            zero_allowed = key in ("upward_force_n", "step_dwell_seconds")
            if not math.isfinite(value) or value < 0 or (value == 0 and not zero_allowed):
                raise ValueError(f"Invalid setting [{section}] {key}")
            values[section][key] = value
    if not config.getboolean("compliance", "preserve_payload_during_operation"):
        raise ValueError("preserve_payload_during_operation must be true")
    values["compliance"]["preserve_payload_during_operation"] = True
    if values["release"]["min_accepted_upward_displacement_mm"] > values["release"]["target_upward_displacement_mm"]:
        raise ValueError("Release minimum height exceeds target height")
    return values


def force_completion(action, dz_mm, elapsed, motor_state, settings, timeout):
    """Return the completion reason or None; raise on an agreed failure condition.

    Bounds are directional. The epsilon only absorbs roundoff at exact
    millimetre boundaries; it is not a physical tolerance band.
    """
    epsilon = 1e-9
    if action == "tighten":
        if -dz_mm > settings["tightening"]["max_downward_displacement_mm"] + epsilon:
            raise RuntimeError("Tightening exceeded maximum downward Z displacement")
        if elapsed > timeout:
            raise RuntimeError("Tightening timeout without timely completion")
        if motor_state == "STALLED":
            return "motor_stall"
    elif action == "withdraw":
        if dz_mm + epsilon >= settings["release"]["target_upward_displacement_mm"]:
            return "release_target_height"
        if elapsed >= timeout:
            if dz_mm + epsilon >= settings["release"]["min_accepted_upward_displacement_mm"]:
                return "release_timeout_minimum_height_accepted"
            raise RuntimeError("Release timeout below minimum upward displacement")
        if motor_state == "STALLED":
            raise RuntimeError("Loosening motor stalled before release completion")
    return None
