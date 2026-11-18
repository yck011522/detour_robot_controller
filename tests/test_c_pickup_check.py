"""Offline checks of the isolated C pickup/tare experiment; no hardware calls."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from game_controller.robot.settings import MOVEMENT_CONFIG, load_settings

source = Path(__file__).resolve().parents[1] / "experiments/e14_robot_step_test/c_pickup_check.py"
spec = importlib.util.spec_from_file_location("c_pickup_check", source)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class CPickupTests(unittest.TestCase):
    def test_preview_never_connects(self):
        with patch.object(check, "LiveRobot") as robot, patch("builtins.print"):
            self.assertEqual(check.main([]), 0)
        robot.assert_not_called()

    def test_xy_allowance_is_per_axis_and_accumulates_across_steps(self):
        guard = check.XYGuard([.1, .2, .3, 0, 0, 0], Mock())
        guard({"tcp": [.103, .197, .3, 0, 0, 0]})
        self.assertAlmostEqual(guard.maximum[0], 3.)
        # Withdrawal must not get another 3 mm from where tightening ended.
        with self.assertRaisesRegex(RuntimeError, "XY allowance"):
            guard({"tcp": [.1031, .2, .32, 0, 0, 0]})

    def test_tare_only_at_high_then_same_guard_through_both_force_steps(self):
        calls, guards = [], []

        def run(args, *, confirmed, xy_guard):
            self.assertTrue(confirmed)
            calls.append(args.action if args.action != "move" else args.pose)
            if xy_guard:
                guards.append(xy_guard)
            return 0

        with patch.object(check, "read_at", return_value={"tcp": [0.] * 6}), \
             patch.object(check, "zero_at_high", side_effect=lambda *a: calls.append("zero")), \
             patch.object(check.step, "run", side_effect=run), patch.object(check.time, "sleep"):
            check.execute(Mock(), MOVEMENT_CONFIG, load_settings(), Mock())
        self.assertEqual(calls, ["c_high", "zero", "c_target", "tighten", "withdraw", "c_high", "ac_home"])
        self.assertEqual(len(guards), 2)
        self.assertIs(guards[0], guards[1])

    def test_failed_withdrawal_does_not_retreat_or_change_payload(self):
        actions = []

        def run(args, **kwargs):
            actions.append((args.action, args.payload))
            return int(args.action == "withdraw")

        with patch.object(check, "read_at", return_value={"tcp": [0.] * 6}), \
             patch.object(check, "zero_at_high"), patch.object(check.step, "run", side_effect=run), \
             patch.object(check.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "No automatic retreat"):
                check.execute(Mock(), MOVEMENT_CONFIG, load_settings(), Mock())
        self.assertEqual(actions, [("move", "keep"), ("move", "keep"), ("tighten", "keep"), ("withdraw", "keep")])

    def test_zero_preserves_payload_and_records_before_and_after(self):
        robot, control, record = Mock(), Mock(), Mock()
        robot.settings = load_settings()
        robot.config_path = MOVEMENT_CONFIG
        before, after = {"tcp": [0.] * 6, "force": [2.] * 6}, {"tcp": [0.] * 6, "force": [0.] * 6}
        module = MagicMock(RTDEControlInterface=Mock(return_value=control))
        with patch.dict(sys.modules, {"rtde_control": module}), \
             patch.object(check, "read_at", side_effect=[before, after]) as read, \
             patch.object(check.time, "sleep"):
            check.zero_at_high(robot, record)
        self.assertEqual([call.args[1] for call in read.call_args_list], ["c_high", "c_high"])
        control.zeroFtSensor.assert_called_once()
        control.setPayload.assert_not_called()
        control.stopScript.assert_called_once()
        control.disconnect.assert_called_once()
        self.assertEqual([call.args[0] for call in record.call_args_list], ["BEFORE_HIGH_ZERO", "AFTER_HIGH_ZERO"])


if __name__ == "__main__":
    unittest.main()
