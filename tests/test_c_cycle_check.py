"""Verify C cycle sequencing and combined lateral-force limit without hardware."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from game_controller.robot.settings import MOVEMENT_CONFIG, load_settings

source = Path(__file__).resolve().parents[1] / "experiments/e14_robot_step_test/c_cycle_check.py"
spec = importlib.util.spec_from_file_location("c_cycle_check", source)
cycle = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(source.parent), *sys.path]):
    spec.loader.exec_module(cycle)


class CycleTests(unittest.TestCase):
    def test_force_guard_checks_resultant_not_individual_axes(self):
        guard = cycle.CycleGuard([0.] * 6, Mock())
        guard({"tcp": [0.] * 6, "force": [24., 32., 0., 0., 0., 0.]})
        with self.assertRaisesRegex(RuntimeError, "exceeds 40 N"):
            guard({"tcp": [0.] * 6, "force": [24.1, 32., 0., 0., 0., 0.]})

    def test_xy_guard_still_applies_below_force_limit(self):
        guard = cycle.CycleGuard([0.] * 6, Mock())
        with self.assertRaisesRegex(RuntimeError, "XY allowance"):
            guard({"tcp": [.004, 0., 0., 0., 0., 0.], "force": [0.] * 6})

    def test_explicit_60n_recovery_guard_uses_combined_force(self):
        guard = cycle.CycleGuard([0.] * 6, Mock(), lateral_force_limit_n=60.)
        guard({"tcp": [0.] * 6, "force": [48., 36., 0., 0., 0., 0.]})
        with self.assertRaisesRegex(RuntimeError, "exceeds 60 N"):
            guard({"tcp": [0.] * 6, "force": [48.1, 36., 0., 0., 0., 0.]})

    def test_cycle_order_high_point_payloads_and_shared_guards(self):
        calls, guards = [], []
        robot = Mock(settings=load_settings())

        def action(args, *, confirmed, xy_guard, xy_hold):
            calls.append(args.pose if args.action == "move" else (args.action, args.motor))
            self.assertTrue(confirmed)
            self.assertEqual(xy_hold, args.action != "move")
            self.assertEqual(args.payload, "keep")
            if xy_guard:
                guards.append(xy_guard)
            return 0

        def payload(robot, old, new, record, *, zone):
            self.assertEqual(zone, "c")
            calls.append((old, new))
            return {"tcp": [0.] * 6}

        with patch.object(cycle, "read_at", return_value={"tcp": [0.] * 6}), \
             patch.object(cycle, "payload_at_high", side_effect=payload), \
             patch.object(cycle.step, "run", side_effect=action), patch.object(cycle.time, "sleep"):
            cycle.execute(robot, MOVEMENT_CONFIG, Mock())
        self.assertEqual(calls, ["c_high", ("tool_only", "tool_only"), "c_target", ("tighten", 1),
                                ("withdraw", 2), "c_high", ("tool_only", "tool_with_floor"), "ac_home",
                                "c_high", ("tool_with_floor", "tool_with_floor"), "c_target", ("tighten", 2),
                                ("withdraw", 1), "c_high", ("tool_with_floor", "tool_only"), "ac_home"])
        self.assertEqual(len(guards), 8)
        self.assertTrue(all(g is guards[0] for g in guards[:4]))
        self.assertTrue(all(g is guards[4] for g in guards[4:]))
        self.assertIsNot(guards[0], guards[4])

    def test_zone_a_uses_only_a_poses_and_a_high_payload_checks(self):
        robot = Mock(settings=load_settings())
        with patch.object(cycle, "read_at", return_value={"tcp": [0.] * 6}), \
             patch.object(cycle, "payload_at_high", return_value={"tcp": [0.] * 6}) as payload, \
             patch.object(cycle.step, "run", return_value=0) as run, patch.object(cycle.time, "sleep"):
            cycle.execute(robot, MOVEMENT_CONFIG, Mock(), zone="a")
        poses = [c.args[0].pose for c in run.call_args_list if c.args[0].action == "move"]
        self.assertEqual(poses, ["a_high", "a_target", "a_high", "ac_home"] * 2)
        self.assertEqual(payload.call_count, 4)
        self.assertTrue(all(c.kwargs["zone"] == "a" for c in payload.call_args_list))


if __name__ == "__main__":
    unittest.main()
