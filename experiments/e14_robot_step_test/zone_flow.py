"""Preview or run one supervised zone operation; no game inventory changes."""

import argparse
import math
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import time

from movement_settings import DEFAULT_CONFIG, load_payload, load_poses, load_settings

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def home_for(zone):
    return "ac_home" if zone in "ac" else "bd_home"


def build_steps(operation, zone, from_target=False):
    home = home_for(zone)
    steps = [] if from_target else [
        (f"Approach {zone.upper()} high", ["move", "--pose", f"{zone}_high", "--payload", "keep"]),
        (f"Approach {zone.upper()} target", ["move", "--pose", f"{zone}_target", "--payload", "keep"]),
    ]
    if operation == "approach":
        return steps
    tightening_motor, release_motor, payload = ((2, 1, "tool_only") if operation == "place"
                                               else (1, 2, "tool_with_floor"))
    steps += [
        ("Tighten with downward compliance", ["tighten", "--motor", str(tightening_motor), "--payload", "keep"]),
        ("Loosen with upward compliance", ["withdraw", "--motor", str(release_motor), "--payload", "keep"]),
        ("Update payload and retreat to high", ["move", "--pose", f"{zone}_high", "--payload", payload]),
        ("Return to associated home", ["move", "--pose", home, "--payload", "keep"]),
    ]
    return steps


def check_start(expected_pose, expected_payload, settings, home_tolerance_deg=None):
    """Recognize the starting endpoint; do not infer a route from arbitrary poses."""
    import step_test
    from rtde_receive import RTDEReceiveInterface
    target = load_poses()[expected_pose]
    expected_mass, cog = load_payload(expected_payload)
    receiver = RTDEReceiveInterface(step_test.tool_protocol.host_from_config(), settings["runtime"]["receive_hz"])
    try:
        first_stamp = receiver.getTimestamp()
        time.sleep(settings["joint_motion"]["settle_seconds"])
        state = step_test.snapshot(receiver)
        if state["timestamp"] <= first_stamp:
            raise RuntimeError("Starting feedback is not updating")
        if state["safety_mode"] != 1 or state["robot_mode"] != 7:
            raise RuntimeError("Starting robot mode/safety is not NORMAL/RUNNING")
        error = step_test.settled_matches(state["joints"], {expected_pose: target})[expected_pose]
        joint = settings["joint_motion"]
        tolerance = joint["arrival_tolerance_deg"] if home_tolerance_deg is None else home_tolerance_deg
        if error > tolerance or max(map(abs, state["joint_velocity"])) > joint["stationary_velocity_rad_s"]:
            raise RuntimeError(f"Start requires stationary {expected_pose}; maximum error {error:.3f} deg")
        # Only compare configured properties; this does not verify physical attachment.
        mass = receiver.getPayload()
        actual_cog = list(receiver.getPayloadCog())
        if not math.isfinite(mass) or abs(mass-expected_mass) > settings["runtime"]["payload_mass_tolerance_kg"]:
            raise RuntimeError(f"Start requires configured {expected_payload} payload; reconcile before testing")
        if len(actual_cog) != 3 or any(not math.isfinite(a) or abs(a-b) > settings["runtime"]["payload_cog_tolerance_m"]
                                      for a,b in zip(actual_cog, cog)):
            raise RuntimeError("Starting configured payload CoG differs from selected payload")
    finally:
        receiver.disconnect()


def validate_steps(steps):
    """Check the whole named route before connecting or moving."""
    poses = load_poses()
    for _, arguments in steps:
        if arguments[0] != "move":
            continue
        name = arguments[arguments.index("--pose") + 1]
        if name not in poses:
            raise ValueError(f"Missing taught pose: {name}")


def run_steps(steps, config_path, settings, transcript, on_step=None, isolate_signals=False):
    """A failed child, including cleanup failure, prevents all subsequent steps."""
    with transcript.open("w", encoding="utf-8") as output:
        output.write(f"CONFIG {config_path}\nSETTINGS {settings}\n")
        for index, (label, arguments) in enumerate(steps):
            if on_step:
                on_step("started", index, label, arguments, None)
            message = f"STEP {index+1}/{len(steps)}: {label}"
            print(message, flush=True)
            output.write(message + "\n")
            output.flush()
            child = subprocess.run(
                [sys.executable, str(HERE / "step_test.py"), *arguments,
                 "--config", str(config_path), "--live"],
                input="RUN\n", text=True, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if isolate_signals else 0,
            )
            if on_step:
                on_step("finished", index, label, arguments, child.returncode)
            if child.returncode:
                print(f"STOPPED: {label} incomplete; inspect {transcript}", flush=True)
                return child.returncode
            print(f"Step {index+1} finished.", flush=True)
            if index < len(steps)-1:
                dwell = settings["joint_motion"]["step_dwell_seconds"]
                output.write(f"DWELL {dwell} seconds\n")
                output.flush()
                time.sleep(dwell)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("place", "pick", "approach"))
    parser.add_argument("zone", choices=list("abcd"))
    parser.add_argument("--from-target", action="store_true", help="operator-confirmed target start")
    parser.add_argument("--payload", choices=("tool_only", "tool_with_floor"),
                        help="required for approach-only; pick/place infer expected starting payload")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--live", action="store_true", help="otherwise preview only; no connections")
    args = parser.parse_args(argv)
    if args.operation == "approach" and (args.from_target or not args.payload):
        parser.error("Approach needs --payload and cannot start from target")
    if args.operation != "approach" and args.payload:
        parser.error("Pick/place determine starting payload; omit --payload")
    settings = load_settings(args.config)
    steps = build_steps(args.operation, args.zone, args.from_target)
    validate_steps(steps)
    for number, (label, arguments) in enumerate(steps, 1):
        print(f"{number}. {label}: {' '.join(arguments)}")
    if not args.live:
        print("PREVIEW ONLY: no hardware connections. Add --live for supervised execution.")
        return 0
    expected = args.payload or ("tool_with_floor" if args.operation == "place" else "tool_only")
    start = f"{args.zone}_target" if args.from_target else home_for(args.zone)
    print(f"Requires {start}, {expected}, correct physical attachment/support, and verified paths.")
    print("Confirm selected zone, mechanisms and paths. Ctrl+C requests stop; keep hardware stops available.")
    if input("Type RUN to execute this sequence: ").strip() != "RUN":
        return 1
    check_start(start, expected, settings)
    directory = HERE / "results"
    directory.mkdir(exist_ok=True)
    transcript = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{args.operation}_{args.zone}.txt"
    print(f"Transcript: {transcript}", flush=True)
    result = run_steps(steps, args.config.resolve(), settings, transcript)
    if result == 0:
        print("Sequence finished. Confirm the physical result; no board inventory was changed.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
