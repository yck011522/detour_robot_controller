"""Display live UR state and save named snapshots without commanding motion."""

import configparser
import math
import os
from pathlib import Path
import sys
import tempfile
import time


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
POSE_FILE = PROJECT_DIRECTORY / "config" / "robot_pose.ini"
HEADER = (
    "# Named robot poses captured by utility/robot_pose_teaching.py.\n"
    "# joint_positions: six joint angles in radians, base through wrist 3.\n"
    "# tcp_pose: x, y, z in metres, then rx, ry, rz rotation vector in radians.\n"
    "# TCP pose is relative to the robot base and uses the active TCP offset.\n\n"
)


def read_snapshot(receiver):
    """Read and validate the latest joint and TCP values."""
    if not receiver.isConnected():
        raise RuntimeError("Robot connection lost.")
    joints = list(receiver.getActualQ())
    tcp = list(receiver.getActualTCPPose())
    if any(len(values) != 6 or not all(math.isfinite(v) for v in values)
           for values in (joints, tcp)):
        raise ValueError("Robot returned an invalid joint position or TCP pose.")
    return joints, tcp


def load_poses(path):
    poses = configparser.ConfigParser(interpolation=None)
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            poses.read_file(stream)
    return poses


def save_pose(path, poses, name, joints, tcp):
    """Replace the file atomically so an interrupted write preserves old poses."""
    poses[name] = {
        "joint_positions": ", ".join(format(v, ".17g") for v in joints),
        "tcp_pose": ", ".join(format(v, ".17g") for v in tcp),
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(HEADER)
            poses.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_key():
    """Poll a Windows console key; other terminals accept a key plus Enter."""
    if os.name == "nt":
        import msvcrt
        return msvcrt.getwch().lower() if msvcrt.kbhit() else ""
    import select
    return sys.stdin.readline().strip().lower() if select.select([sys.stdin], [], [], 0)[0] else ""


def teach(receiver, path=POSE_FILE):
    print("Live joint angles (rad) and TCP (m, rotation vector rad).")
    print("S: capture and name a pose | Q: quit | Ctrl+C: quit")
    if os.name != "nt":
        print("Press Enter after each command.")
    while True:
        joints, tcp = read_snapshot(receiver)
        display = "Joints: " + ", ".join(f"{v: .5f}" for v in joints)
        display += " | TCP: " + ", ".join(f"{v: .5f}" for v in tcp)
        print("\r" + display, end="", flush=True)
        key = read_key()
        if key == "q":
            print()
            return
        if key == "s":
            # Capture before prompting: naming must not change the snapshot.
            joints, tcp = read_snapshot(receiver)
            print("\nCaptured joints:", joints)
            print("Captured TCP:", tcp)
            name = input("Pose name (blank cancels): ").strip()
            if not name:
                continue
            if any(c in name for c in "[]\r\n") or name == "DEFAULT":
                print("Invalid name: avoid brackets, line breaks, and DEFAULT.")
                continue
            poses = load_poses(path)
            if poses.has_section(name):
                if input(f"Replace '{name}'? [y/N]: ").strip().lower() != "y":
                    print("Cancelled.")
                    continue
            save_pose(path, poses, name, joints, tcp)
            print(f"Saved '{name}' to {path}")
        time.sleep(0.1)


def main():
    receiver = None
    try:
        if not sys.stdin.isatty():
            raise RuntimeError("Run this utility in an interactive terminal.")
        configuration = configparser.ConfigParser()
        with (PROJECT_DIRECTORY / "config" / "address.ini").open(encoding="utf-8") as stream:
            configuration.read_file(stream)
        host = configuration["robotic_arm"]["host"]
        from rtde_receive import RTDEReceiveInterface
        print(f"Connecting to {host} using RTDE receive only...", flush=True)
        receiver = RTDEReceiveInterface(host, 125.0)
        teach(receiver)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nTeaching ended.")
        return 0
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
    finally:
        if receiver is not None:
            receiver.disconnect()
            print("RTDE receive connection closed.")


if __name__ == "__main__":
    raise SystemExit(main())
