"""One logged A/C pickup/place cycle: UR-held XY, high-point payload/tare, 40 N lateral guard."""

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from c_pickup_check import XYGuard
from c_placement_check import payload_at_high, read_at
from game_controller.robot import step
from game_controller.robot.live import LiveRobot
from game_controller.robot.settings import MOVEMENT_CONFIG, load_poses, load_settings


class CycleGuard(XYGuard):
    def __init__(self, tcp, record, *, lateral_force_limit_n=40.):
        if not math.isfinite(lateral_force_limit_n) or lateral_force_limit_n <= 0:
            raise ValueError("Lateral force limit must be finite and positive")
        super().__init__(tcp, record)
        self.lateral_force_limit_n = lateral_force_limit_n
        self.label = ""
        self.max_lateral_force = 0.

    def __call__(self, state):
        lateral = math.hypot(*state["force"][:2])
        self.max_lateral_force = max(self.max_lateral_force, lateral)
        self.record("CYCLE_SAMPLE", label=self.label, lateral_force_n=lateral,
                    delta_xy_mm=[1000 * (state["tcp"][i] - self.reference[i]) for i in range(2)], **state)
        super().__call__(state)
        if lateral > self.lateral_force_limit_n:
            self.record("LATERAL_FORCE_LIMIT_EXCEEDED", label=self.label, lateral_force_n=lateral, **state)
            raise RuntimeError(f"Lateral force {lateral:.2f} N exceeds {self.lateral_force_limit_n:g} N; stopping without retry")


def execute(robot, config, record, *, zone="c"):
    if zone not in ("a", "c"):
        raise ValueError("This cycle supports only A/C and AC-home")
    high_pose, target_pose = f"{zone}_high", f"{zone}_target"
    record("CYCLE_START", zone=zone, **read_at(robot, "ac_home", "tool_only"))

    def action(label, arguments, guard=None):
        record("TEST_STEP", label=label)
        if guard:
            guard.label = label
        args = step.parser().parse_args([*arguments, "--config", str(config), "--live"])
        code = step.run(args, confirmed=True, xy_guard=guard,
                        xy_hold=arguments[0] in ("tighten", "withdraw"))
        if code:
            raise RuntimeError(f"{label} incomplete; no automatic retry/retreat. Inspect attachment.")
        if guard:
            record("GUARD_SUMMARY", label=label, max_abs_xy_mm=guard.maximum,
                   max_lateral_force_n=guard.max_lateral_force)
        time.sleep(robot.settings["joint_motion"]["step_dwell_seconds"])

    action(f"Pickup: approach {zone.upper()} high", ["move", "--pose", high_pose])
    high = payload_at_high(robot, "tool_only", "tool_only", record, zone=zone)
    pickup = CycleGuard(high["tcp"], record)
    action(f"Pickup: approach {zone.upper()} target", ["move", "--pose", target_pose], pickup)
    action("Pickup: tighten M1, XY held", ["tighten", "--motor", "1"], pickup)
    action("Pickup: withdraw M2, XY held", ["withdraw", "--motor", "2"], pickup)
    action(f"Pickup: retreat loaded to {zone.upper()} high", ["move", "--pose", high_pose], pickup)
    payload_at_high(robot, "tool_only", "tool_with_floor", record, zone=zone)
    action("Pickup: return loaded to AC home", ["move", "--pose", "ac_home"])
    record("PICKUP_HOME", **read_at(robot, "ac_home", "tool_with_floor"))

    action(f"Placement: approach {zone.upper()} high", ["move", "--pose", high_pose])
    high = payload_at_high(robot, "tool_with_floor", "tool_with_floor", record, zone=zone)
    placement = CycleGuard(high["tcp"], record)
    action(f"Placement: approach {zone.upper()} target", ["move", "--pose", target_pose], placement)
    action("Placement: tighten M2, XY held", ["tighten", "--motor", "2"], placement)
    action("Placement: withdraw M1, XY held", ["withdraw", "--motor", "1"], placement)
    action(f"Placement: retreat empty to {zone.upper()} high", ["move", "--pose", high_pose], placement)
    payload_at_high(robot, "tool_with_floor", "tool_only", record, zone=zone)
    action("Placement: return empty to AC home", ["move", "--pose", "ac_home"])
    record(f"{zone.upper()}_CYCLE_COMPLETE", **read_at(robot, "ac_home", "tool_only"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone", choices=("a", "c"), default="c")
    parser.add_argument("--live", action="store_true", help="otherwise preview without connections")
    parser.add_argument("--config", type=Path, default=MOVEMENT_CONFIG)
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    poses = load_poses()
    for pose in ("ac_home", f"{args.zone}_high", f"{args.zone}_target"):
        if pose not in poses:
            raise ValueError(f"Missing taught pose: {pose}")
    print(f"AC home -> pick {args.zone.upper()} -> AC home loaded -> place {args.zone.upper()} -> AC home empty.")
    print("Only Z compliant; X/Y held. Contact phases: +/-3 mm XY, sqrt(Fx^2+Fy^2) <=40 N.")
    print(f"Payload/tare at stationary {args.zone.upper()}-high only; preserve reference during contact/retreat.")
    if not args.live:
        print("PREVIEW ONLY: no hardware connections.")
        return 0
    print(f"Requires game stopped, empty tool at AC home, tool-only payload, {args.zone.upper()} floor installed.")
    print("Any failure stops without automatic retreat. Game inventory and arm power are unchanged.")
    if input(f"Confirm physical state and {args.zone.upper()} paths; type RUN: ").strip() != "RUN":
        return 1
    directory = ROOT / "logs/robot"
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{args.zone}_cycle_check.jsonl"
    print(f"REPORT: {report}\nCSV: {report.with_suffix('.csv')}", flush=True)
    with report.open("x", encoding="utf-8") as output, report.with_suffix(".csv").open("x", newline="", encoding="utf-8") as csv_output:
        writer = csv.writer(csv_output)
        writer.writerow(["time", "step", "controller_timestamp", "x_m", "y_m", "z_m", "dx_mm", "dy_mm", "fx_n", "fy_n", "fz_n", "lateral_force_n"])

        def record(event, **data):
            stamp = datetime.now().astimezone().isoformat()
            output.write(json.dumps(dict(time=stamp, event=event, **data)) + "\n")
            output.flush()
            if event == "CYCLE_SAMPLE":
                writer.writerow([stamp, data["label"], data["timestamp"], *data["tcp"][:3],
                                 *data["delta_xy_mm"], *data["force"][:3], data["lateral_force_n"]])
                csv_output.flush()
            else:
                print(json.dumps(dict(time=stamp, event=event, **data)), flush=True)

        record("CYCLE_SETTINGS", zone=args.zone, config=str(args.config.resolve()), settings=settings,
               lateral_force_limit_n=40, xy_limit_mm=3)
        try:
            execute(LiveRobot(args.config), args.config.resolve(), record, zone=args.zone)
        except (Exception, KeyboardInterrupt) as error:
            record("TEST_STOPPED", reason=str(error) or "Ctrl+C", reconciliation_required=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
