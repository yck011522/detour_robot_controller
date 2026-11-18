"""Recover a confirmed-clear C floor to high, then place C with only Z compliant."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from c_pickup_check import XYGuard
from game_controller.robot import step
from game_controller.robot.live import LiveRobot
from game_controller.robot.settings import MOVEMENT_CONFIG, load_payload, load_settings, read_ini


def read_at(robot, pose, payload):
    robot.check_start(pose, payload)
    receiver = robot.receiver()
    try:
        return robot.fresh_state(receiver)
    finally:
        receiver.disconnect()


def payload_at_high(robot, previous, payload, record, *, zone="c"):
    """Update the supported load and tare only at the selected clear high pose."""
    from rtde_control import RTDEControlInterface
    settle = read_ini(robot.config_path).getfloat("runtime", "ft_zero_settle_seconds")
    if not math.isfinite(settle) or settle <= 0:
        raise ValueError("FT zero settle time must be finite and positive")
    record("BEFORE_HIGH_PAYLOAD", zone=zone, payload=payload, **read_at(robot, f"{zone}_high", previous))
    control = None
    try:
        control = RTDEControlInterface(step.tool_protocol.host_from_config())
        mass, cog = load_payload(payload)
        if not control.setPayload(mass, cog):
            raise RuntimeError("High-point payload update rejected")
        time.sleep(settle)
        if not control.zeroFtSensor():
            raise RuntimeError("High-point FT zero rejected")
        time.sleep(settle)
    finally:
        if control:
            try:
                if control.stopScript() is False:
                    raise RuntimeError("High-point setup program stop unconfirmed")
            finally:
                control.disconnect()
    state = read_at(robot, f"{zone}_high", payload)
    record("AFTER_HIGH_PAYLOAD_ZERO", zone=zone, payload=payload, **state)
    return state


def execute(robot, config, record):
    # This entry point is specifically for the stopped pickup, confirmed clear
    # by the operator. Preserve its old payload until the first high-point stop.
    record("CONFIRMED_CLEAR_START", **read_at(robot, "c_target", "tool_only"))

    def action(label, arguments, guard=None, hold=False):
        record("TEST_STEP", label=label)
        args = step.parser().parse_args([*arguments, "--config", str(config), "--live"])
        code = step.run(args, confirmed=True, xy_guard=guard, xy_hold=hold)
        if code:
            raise RuntimeError(f"{label} incomplete. No automatic retry/retreat; inspect attachment.")
        if guard:
            record("XY_SUMMARY", label=label, delta_xy_mm=guard.last, max_abs_xy_mm=guard.maximum)
        time.sleep(robot.settings["joint_motion"]["step_dwell_seconds"])

    action("Raise confirmed-clear floor to C high", ["move", "--pose", "c_high"])
    high = payload_at_high(robot, "tool_only", "tool_with_floor", record)
    guard = XYGuard(high["tcp"], record)
    record("PLACEMENT_XY_REFERENCE", **high)
    action("Approach C target", ["move", "--pose", "c_target"], guard)
    action("Tighten M2 downward, XY held", ["tighten", "--motor", "2"], guard, True)
    action("Loosen M1 upward, XY held", ["withdraw", "--motor", "1"], guard, True)
    action("Retreat empty tool to C high, preserve payload reference", ["move", "--pose", "c_high"], guard)
    payload_at_high(robot, "tool_with_floor", "tool_only", record)
    action("Return empty to AC home", ["move", "--pose", "ac_home"])
    record("C_PLACEMENT_COMPLETE", held=None, floor="C", home="ac_home")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="otherwise preview only")
    parser.add_argument("--config", type=Path, default=MOVEMENT_CONFIG)
    args = parser.parse_args(argv)
    load_settings(args.config)
    print("Confirmed-clear pickup position -> C high -> loaded payload + zero -> C target")
    print("-> M2 tighten (Z compliant, XY held) -> M1 withdraw (Z compliant, XY held)")
    print("-> C high -> tool-only payload + zero -> AC home")
    print("Placement approach/force steps/retreat share a +/-3 mm XY stop guard.")
    if not args.live:
        print("PREVIEW ONLY: no robot connections.")
        return 0
    print("Requires the stopped C-pickup position, floor securely attached and CLEAR,")
    print("NORMAL/RUNNING, old tool-only payload still configured, and the game stopped.")
    print("Recovery lift is position controlled; placement X/Y holds remove self-alignment.")
    if input("Confirm floor attachment, clearance and C path; type RUN: ").strip() != "RUN":
        return 1
    directory = ROOT / "logs/robot"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_c_placement_check.jsonl"
    print(f"Test report: {path}")
    with path.open("x", encoding="utf-8") as output:
        def record(event, **data):
            line = json.dumps(dict(time=datetime.now().astimezone().isoformat(), event=event, **data))
            output.write(line + "\n")
            output.flush()
            print(line, flush=True)

        try:
            execute(LiveRobot(args.config), args.config.resolve(), record)
        except (Exception, KeyboardInterrupt) as error:
            record("TEST_STOPPED", reason=str(error) or "Ctrl+C", reconciliation_required=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
