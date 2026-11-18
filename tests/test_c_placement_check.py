"""Offline verification of C placement with high-point payload and XY hold."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from game_controller.robot.settings import MOVEMENT_CONFIG, load_settings

source = Path(__file__).resolve().parents[1] / "experiments/e14_robot_step_test/c_placement_check.py"
spec = importlib.util.spec_from_file_location("c_placement_check", source)
check = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(source.parent), *sys.path]):
    spec.loader.exec_module(check)


class PlacementTests(unittest.TestCase):
    def test_payload_changes_only_at_high_and_xy_guard_covers_approach_and_retract(self):
        robot = Mock(settings=load_settings())
        calls, guarded = [], []

        def run(args, *, confirmed, xy_guard, xy_hold):
            self.assertTrue(confirmed)
            self.assertEqual(args.payload, "keep")
            calls.append(args.pose if args.action == "move" else args.action)
            self.assertEqual(xy_hold, args.action != "move")
            if xy_guard:
                guarded.append(xy_guard)
            return 0

        def payload(robot, old, new, record):
            calls.append(new)
            return {"tcp": [0.] * 6}

        with patch.object(check, "read_at", return_value={"tcp": [0.] * 6}), \
             patch.object(check, "payload_at_high", side_effect=payload), \
             patch.object(check.step, "run", side_effect=run), patch.object(check.time, "sleep"):
            check.execute(robot, MOVEMENT_CONFIG, Mock())
        self.assertEqual(calls, ["c_high", "tool_with_floor", "c_target", "tighten", "withdraw", "c_high", "tool_only", "ac_home"])
        self.assertEqual(len(guarded), 4)
        self.assertTrue(all(guard is guarded[0] for guard in guarded))

    def test_failed_tightening_does_not_loosen_floor_or_retreat(self):
        robot = Mock(settings=load_settings())
        calls = []

        def run(args, **kwargs):
            calls.append(args.pose if args.action == "move" else args.action)
            return int(args.action == "tighten")

        with patch.object(check, "read_at", return_value={"tcp": [0.] * 6}), \
             patch.object(check, "payload_at_high", return_value={"tcp": [0.] * 6}) as payload, \
             patch.object(check.step, "run", side_effect=run), patch.object(check.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "No automatic retry"):
                check.execute(robot, MOVEMENT_CONFIG, Mock())
        self.assertEqual(calls, ["c_high", "c_target", "tighten"])
        payload.assert_called_once()

    def test_high_point_payload_then_explicit_zero_without_motion(self):
        robot = Mock(config_path=MOVEMENT_CONFIG)
        control = Mock()
        module = MagicMock(RTDEControlInterface=Mock(return_value=control))
        with patch.dict(sys.modules, {"rtde_control": module}), \
             patch.object(check, "read_at", return_value={"tcp": [0.] * 6}) as read, \
             patch.object(check.time, "sleep"):
            check.payload_at_high(robot, "tool_only", "tool_with_floor", Mock())
        self.assertEqual([c.args[1:] for c in read.call_args_list], [("c_high", "tool_only"), ("c_high", "tool_with_floor")])
        control.setPayload.assert_called_once_with(2.11, [-.001, .035, .026])
        self.assertEqual([c[0] for c in control.mock_calls], ["setPayload", "zeroFtSensor", "stopScript", "disconnect"])


if __name__ == "__main__":
    unittest.main()
