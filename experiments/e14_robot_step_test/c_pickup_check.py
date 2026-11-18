"""Isolated AC-home -> C pickup -> AC-home check with high-point tare and XY guard."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from game_controller.robot import step
from game_controller.robot.live import LiveRobot
from game_controller.robot.settings import MOVEMENT_CONFIG, load_poses, load_settings, read_ini


def plan():
    return [
        ("Move to C high", ["move", "--pose", "c_high", "--payload", "keep"]),
        ("Zero FT at stationary, contact-free C high", None),
        ("Move to C target", ["move", "--pose", "c_target", "--payload", "keep"]),
        ("Tighten M1 with downward compliance", ["tighten", "--motor", "1"]),
        ("Loosen M2 with upward compliance", ["withdraw", "--motor", "2"]),
        ("Set loaded payload and retreat to C high", ["move", "--pose", "c_high", "--payload", "tool_with_floor"]),
        ("Return to AC home carrying C floor", ["move", "--pose", "ac_home", "--payload", "keep"]),
    ]


class XYGuard:
    """One base-frame XY reference shared across all guarded test steps."""
    def __init__(self, tcp, record):
        self.reference = tcp[:2]
        self.record = record
        self.maximum = [0., 0.]
        self.last = [0., 0.]

    def __call__(self, state):
        self.last = [(state["tcp"][i] - self.reference[i]) * 1000 for i in range(2)]
        self.maximum = [max(a, abs(b)) for a, b in zip(self.maximum, self.last)]
        if max(map(abs, self.last)) > 3.0 + 1e-9:
            self.record("XY_LIMIT_EXCEEDED", delta_xy_mm=self.last, **state)
            raise RuntimeError(f"Zone test exceeded +/-3 mm XY allowance: {self.last} mm")


def read_at(robot, pose):
    robot.check_start(pose, "tool_only")
    receiver = robot.receiver()
    try:
        return robot.fresh_state(receiver)
    finally:
        receiver.disconnect()


def zero_at_high(robot, record):
    """Keep the tool-only payload; tare only while stationary and free of contact."""
    from rtde_control import RTDEControlInterface
    settle = read_ini(robot.config_path).getfloat("runtime", "ft_zero_settle_seconds")
    if not math.isfinite(settle) or settle <= 0:
        raise ValueError("FT zero settle time must be finite and positive")
    before = read_at(robot, "c_high")
    record("BEFORE_HIGH_ZERO", **before)
    control = None
    try:
        control = RTDEControlInterface(step.tool_protocol.host_from_config())
        if not control.zeroFtSensor():
            raise RuntimeError("FT zero command rejected")
        time.sleep(settle)
    finally:
        if control:
            try:
                if control.stopScript() is False:
                    raise RuntimeError("Zeroing program stop unconfirmed")
            finally:
                control.disconnect()
    after = read_at(robot, "c_high")
    record("AFTER_HIGH_ZERO", **after)


def execute(robot, config, settings, record, *, skip_zero=False):
    read_at(robot, "ac_home")
    guard = None
    for label, arguments in plan():
        record("TEST_STEP", label=label)
        if arguments is None:
            if skip_zero:
                record("HIGH_ZERO_SKIPPED")
            else:
                zero_at_high(robot, record)
        else:
            force_action = arguments[0] in ("tighten", "withdraw")
            if arguments[0] == "tighten":
                target = read_at(robot, "c_target")
                record("XY_REFERENCE", **target)
                guard = XYGuard(target["tcp"], record)
            args = step.parser().parse_args([*arguments, "--config", str(config), "--live"])
            code = step.run(args, confirmed=True, xy_guard=guard if force_action else None)
            if code:
                raise RuntimeError(f"{label} failed. No automatic retreat/home; inspect the mechanism.")
            if force_action:
                record("XY_SUMMARY", label=label, delta_xy_mm=guard.last, max_abs_xy_mm=guard.maximum)
        time.sleep(settings["joint_motion"]["step_dwell_seconds"])
    record("C_PICKUP_COMPLETE", held="C floor", home="ac_home")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="otherwise preview without connections")
    parser.add_argument("--skip-zero", action="store_true", help="comparison run only; keep the same XY guard")
    parser.add_argument("--config", type=Path, default=MOVEMENT_CONFIG)
    args = parser.parse_args(argv)
    settings, poses = load_settings(args.config), load_poses()
    for required in ("ac_home", "c_high", "c_target"):
        if required not in poses:
            raise ValueError(f"Missing taught pose: {required}")
    for index, (label, _) in enumerate(plan(), 1):
        print(f"{index}. {label}")
    print(f"High-point zero: {'SKIPPED' if args.skip_zero else 'enabled'}; shared XY stop boundary: +/-3 mm per axis.")
    if not args.live:
        print("PREVIEW ONLY. Add --live for the supervised pickup test.")
        return 0
    print("Stop the game first. Requires powered/brake-released arm at AC home, empty tool,")
    print("configured tool-only payload, C floor installed, and a clear C-high pose/path.")
    print("Success leaves C floor HELD at AC home; game board inventory is not updated.")
    print("Ctrl+C stops the active step; faults never trigger an automatic retreat.")
    if input("Confirm physical state and paths; type RUN: ").strip() != "RUN":
        return 1
    directory = ROOT / "logs/robot"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_c_pickup_check.jsonl"
    print(f"Test report: {path}")
    with path.open("x", encoding="utf-8") as output:
        def record(event, **data):
            line = json.dumps(dict(time=datetime.now().astimezone().isoformat(), event=event, **data))
            output.write(line + "\n")
            output.flush()
            print(line, flush=True)

        record("C_PICKUP_TEST", config=str(args.config.resolve()), settings=settings, skip_zero=args.skip_zero)
        try:
            execute(LiveRobot(args.config), args.config.resolve(), settings, record, skip_zero=args.skip_zero)
        except (Exception, KeyboardInterrupt) as error:
            record("TEST_STOPPED", reason=str(error) or "Ctrl+C", reconciliation_required=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
