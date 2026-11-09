"""Read current UR joint angles and configured TCP pose. No motion/control script."""

import configparser
import math
from pathlib import Path

from rtde_receive import RTDEReceiveInterface


config = configparser.ConfigParser(interpolation=None)
with (Path(__file__).resolve().parents[2] / "config/address.ini").open(encoding="utf-8") as source:
    config.read_file(source)
host = config["robotic_arm"]["host"]

print(f"Reading {host} through RTDE receive only...", flush=True)
receiver = RTDEReceiveInterface(host, 10.0)
try:
    joints = list(receiver.getActualQ())
    tcp = list(receiver.getActualTCPPose())
    if len(joints) != 6 or len(tcp) != 6 or not all(math.isfinite(v) for v in joints + tcp):
        raise RuntimeError("Invalid joint/pose feedback")
    print("Controller timestamp (s):", receiver.getTimestamp())
    print("Robot / safety mode (raw):", receiver.getRobotMode(), receiver.getSafetyMode())
    print("Joints [rad]:", [round(v, 8) for v in joints])
    print("Joints [deg]:", [round(math.degrees(v), 4) for v in joints])
    print("TCP [x, y, z, rx, ry, rz] (metres / rotation-vector radians):")
    print([round(v, 8) for v in tcp])
    print("TCP XYZ [mm]:", [round(v * 1000, 3) for v in tcp[:3]])
finally:
    receiver.disconnect()
