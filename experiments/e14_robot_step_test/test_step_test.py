import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import sys
import tempfile
import itertools

sys.path.insert(0, str(Path(__file__).resolve().parent))
from movement_settings import force_completion, load_settings
import zone_flow
import home_travel

spec = importlib.util.spec_from_file_location("step_test", Path(__file__).with_name("step_test.py"))
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)


class StepTest(unittest.TestCase):
    def test_joint_winding_preserved(self):
        errors = step.settled_matches([0.]*6, {"wound": [6.283185307179586] + [0.]*5})
        self.assertAlmostEqual(errors["wound"], 360.)

    def test_release_boundaries(self):
        settings = load_settings()
        for dz, elapsed, accepted in ((20, 4, True), (20.1, 4, True),
                                      (19.9, 29.9, False), (10, 29.9, False),
                                      (10, 30, True), (15, 30, True), (20, 30, True)):
            with self.subTest(dz=dz, elapsed=elapsed):
                result = force_completion("withdraw", dz, elapsed, "LOOSENING", settings, 30)
                self.assertEqual(result is not None, accepted)
        for dz in (9.999, 0, -10):
            with self.subTest(dz=dz), self.assertRaises(RuntimeError):
                force_completion("withdraw", dz, 30, "LOOSENING", settings, 30)
        # Only the current displacement counts; previously attained height is not stored.
        self.assertIsNone(force_completion("withdraw", 15, 5, "LOOSENING", settings, 30))
        with self.assertRaises(RuntimeError):
            force_completion("withdraw", 5, 30, "LOOSENING", settings, 30)

    def test_tightening_direction_depth_timeout_and_stall(self):
        settings = load_settings()
        self.assertIsNone(force_completion("tighten", -20, 5, "TIGHTENING", settings, 30))
        self.assertIsNone(force_completion("tighten", 25, 5, "TIGHTENING", settings, 30))
        self.assertEqual(force_completion("tighten", -5, 3, "STALLED", settings, 30), "motor_stall")
        for dz, elapsed, motor in ((-20.001, 5, "TIGHTENING"), (-21, 5, "STALLED"), (-5, 30.1, "TIGHTENING")):
            with self.subTest(dz=dz, elapsed=elapsed), self.assertRaises(RuntimeError):
                force_completion("tighten", dz, elapsed, motor, settings, 30)

    def test_config_validation(self):
        original = step.DEFAULT_CONFIG.read_text()
        for old, new in (("speed_deg_s = 5.0", "speed_deg_s = nan"),
                         ("min_accepted_upward_displacement_mm = 10.0", "min_accepted_upward_displacement_mm = 21.0"),
                         ("preserve_payload_during_operation = true", "preserve_payload_during_operation = false")):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "movement.ini"
                path.write_text(original.replace(old,new))
                with self.assertRaises(ValueError):
                    load_settings(path)

    def test_all_zone_recipes(self):
        for zone in "abcd":
            for operation, motors, payload in (("pick", (1,2), "tool_with_floor"), ("place", (2,1), "tool_only")):
                with self.subTest(zone=zone, operation=operation):
                    steps = zone_flow.build_steps(operation, zone)
                    self.assertEqual(steps[0][1][2], zone+"_high")
                    self.assertEqual(steps[1][1][2], zone+"_target")
                    self.assertEqual(int(steps[2][1][2]), motors[0])
                    self.assertEqual(int(steps[3][1][2]), motors[1])
                    self.assertEqual(steps[4][1][-1], payload)
                    self.assertEqual(steps[5][1][2], "ac_home" if zone in "ac" else "bd_home")
                    self.assertEqual(len(zone_flow.build_steps(operation,zone,True)), 4)

    def test_flow_stops_on_failed_step_and_dwells_only_after_success(self):
        steps = zone_flow.build_steps("place", "b")
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(zone_flow.subprocess, "run", side_effect=[MagicMock(returncode=0), MagicMock(returncode=1)]) as run, \
             patch.object(zone_flow.time, "sleep") as sleep:
            result = zone_flow.run_steps(steps, step.DEFAULT_CONFIG, load_settings(), Path(folder)/"flow.txt")
            self.assertEqual(result, 1)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(.5)

    def test_preview_never_connects(self):
        with patch.object(zone_flow, "check_start") as check, patch.object(zone_flow, "run_steps") as run:
            self.assertEqual(zone_flow.main(["pick", "a"]), 0)
            check.assert_not_called()
            run.assert_not_called()

    def test_directed_home_routes_use_cross_home_profile(self):
        for direction, prefix, home in (("bd-to-ac", "bd_ac", "ac_home"), ("ac-to-bd", "ac_bd", "bd_home")):
            steps = home_travel.build_steps(direction)
            self.assertEqual([a[2] for _, a in steps], [f"{prefix}_{i}" for i in range(1,6)] + [home])
            for _, arguments in steps:
                self.assertEqual(arguments[-2:], ["--profile", "cross_home"])
                self.assertNotIn("--joint-speed-deg-s", arguments)
            zone_flow.validate_steps(steps)

    def test_route_preflight_rejects_missing_pose(self):
        with patch.object(zone_flow, "load_poses", return_value={}):
            with self.assertRaises(ValueError):
                zone_flow.validate_steps(zone_flow.build_steps("place", "c"))

    def test_move_profiles_and_runtime_settings_are_loaded(self):
        for profile, expected_speed, expected_timeout in (("local", 5., 60.), ("cross_home", 10., 120.)):
            with self.subTest(profile=profile):
                receiver, control, tool = MagicMock(), MagicMock(), MagicMock()
                receiver.getActualQ.return_value = [0.]*6
                receiver.getActualQd.return_value = [0.]*6
                receiver.getActualTCPPose.return_value = [0.]*6
                receiver.getActualTCPForce.return_value = [0.]*6
                receiver.getTimestamp.side_effect = range(100)
                receiver.getRobotMode.return_value = 7
                receiver.getSafetyMode.return_value = 1
                control.getAsyncOperationProgressEx.return_value.isAsyncOperationRunning.return_value = False
                rtde = MagicMock(RTDEReceiveInterface=MagicMock(return_value=receiver))
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder)/"movement.ini"
                    path.write_text(step.DEFAULT_CONFIG.read_text().replace("receive_hz = 125.0", "receive_hz = 62.5"))
                    args = step.parser().parse_args(["move", "--pose", "bd_home", "--payload", "keep", "--profile", profile,
                                                    "--config", str(path), "--live"])
                    with patch.object(step, "Log"), patch.object(step, "load_poses", return_value={"bd_home": [0.]*6}), \
                         patch.object(step.time, "sleep"), patch.object(step.time, "monotonic", side_effect=itertools.count(0, .1)), \
                         patch("builtins.input", return_value="RUN"), \
                         patch.dict(sys.modules, {"rtde_receive": rtde,
                             "rtde_control": MagicMock(RTDEControlInterface=MagicMock(return_value=control))}), \
                         patch.object(step.tool_protocol, "LineClient", return_value=tool), \
                         patch.object(step.tool_protocol, "status", return_value={"states": ["IDLE", "IDLE"], "pwm_percent": 0}):
                        self.assertEqual(step.run(args), 0)
                        self.assertEqual(args.seconds, expected_timeout)
                        self.assertAlmostEqual(control.moveJ.call_args.args[1], expected_speed * 3.141592653589793 / 180)
                        self.assertEqual(rtde.RTDEReceiveInterface.call_args.args[1], 62.5)
                        control.setWatchdog.assert_called_once_with(3.)
                        control.setPayload.assert_not_called()
                        control.forceMode.assert_not_called()
                        control.stopJ.assert_called_once_with(1.)

    def test_force_actions_stop_on_success_and_error(self):
        for action, states, expected, release_height in (
            ("tighten", ["TIGHTENING", "STALLED"], 0, None),
            ("tighten", ["IDLE"], 1, None),
            ("withdraw", ["STALLED"], 1, None),
            ("loosen-compliant", ["LOOSENING"], 0, None),
            ("withdraw", ["LOOSENING"], 0, .015),
            ("withdraw", ["LOOSENING"], 1, .009),
        ):
            with self.subTest(action=action, states=states):
                receiver, control, tool = MagicMock(), MagicMock(), MagicMock()
                receiver.getActualQ.return_value = [0.]*6
                receiver.getActualQd.return_value = [0.]*6
                receiver.getActualTCPPose.return_value = [0.]*6
                if release_height is not None:
                    receiver.getActualTCPPose.side_effect = [[0.]*6]*3 + [[0,0,release_height,0,0,0]]*2
                receiver.getActualTCPForce.return_value = [0.]*6
                receiver.getTimestamp.side_effect = range(100)
                receiver.getRobotMode.return_value = 7
                receiver.getSafetyMode.return_value = 1
                receiver.getPayload.return_value = 2.110
                receiver.getPayloadCog.return_value = [-.001, .035, .026]
                idle = {"states": ["IDLE", "IDLE"], "pwm_percent": 0}
                statuses = [idle] + [{"states": [s, "IDLE"]} for s in states] + [idle]
                payload = "keep"
                args = step.parser().parse_args([action, "--motor", "1", "--payload", payload, "--live"])
                if action == "loosen-compliant" or release_height is not None:
                    args.seconds = .05
                ordering = MagicMock()
                ordering.attach_mock(control, "control")
                ordering.attach_mock(tool, "tool")
                with tempfile.TemporaryDirectory() as folder, \
                     patch.object(step, "Log") as log_type, \
                     patch.object(step.time, "sleep"), \
                     patch.object(step.time, "monotonic", side_effect=itertools.count(0, .1)), \
                     patch("builtins.input", return_value="RUN"), \
                     patch.dict(sys.modules, {
                         "rtde_receive": MagicMock(RTDEReceiveInterface=MagicMock(return_value=receiver)),
                         "rtde_control": MagicMock(RTDEControlInterface=MagicMock(return_value=control))}), \
                     patch.object(step.tool_protocol, "LineClient", return_value=tool), \
                     patch.object(step.tool_protocol, "status", side_effect=statuses):
                    self.assertEqual(step.run(args), expected)
                    tool.command.assert_any_call("STOP", "OK STOP")
                    control.stopL.assert_called_once()
                    control.stopScript.assert_called_once()
                    control.forceModeStop.assert_called_once()
                    calls = ordering.mock_calls
                    force_index = next(i for i, c in enumerate(calls) if c[0] == "control.forceMode")
                    motor_index = next(i for i, c in enumerate(calls) if c[0] == "tool.command" and c.args[0].startswith(("TIGHTEN", "LOOSEN")))
                    self.assertLess(force_index, motor_index)
                    if payload == "keep":
                        control.setPayload.assert_not_called()
                        control.zeroFtSensor.assert_not_called()
                        if action == "loosen-compliant":
                            self.assertEqual(control.forceMode.call_args.args[2], [0,0,0.,0,0,0])
                        if action == "withdraw":
                            self.assertEqual(control.forceMode.call_args.args[2][2], 5.)


if __name__ == "__main__":
    unittest.main()
