"""Robot process: settings, single-action runner (mocked RTDE), operations and worker events."""

import itertools
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from game_controller.robot import step, worker
from game_controller.robot.settings import MOVEMENT_CONFIG, force_completion, load_poses, load_settings


class SettingsTests(unittest.TestCase):
    def test_joint_winding_preserved(self):
        errors = step.settled_matches([0.] * 6, {"wound": [6.283185307179586] + [0.] * 5})
        self.assertAlmostEqual(errors["wound"], 360.)

    def test_configured_speeds_are_doubled_and_force_actions_unchanged(self):
        settings = load_settings()
        self.assertEqual((settings["joint_motion"]["speed_deg_s"], settings["cross_home"]["speed_deg_s"]), (10., 20.))
        self.assertEqual(settings["compliance"]["translation_speed_mm_s"], 5.)
        self.assertEqual(settings["tightening"]["downward_force_n"], 10.)
        self.assertEqual((settings["placement_release"]["upward_force_n"],
                          settings["pickup_release"]["upward_force_n"]), (5., 15.))

    def test_release_boundaries(self):
        settings = load_settings()
        for dz, elapsed, accepted in ((20, 4, True), (20.1, 4, True), (19.9, 29.9, False),
                                      (10, 29.9, False), (10, 30, True), (15, 30, True), (20, 30, True)):
            with self.subTest(dz=dz, elapsed=elapsed):
                self.assertEqual(force_completion("withdraw", dz, elapsed, "LOOSENING", settings, 30) is not None,
                                 accepted)
        for dz in (9.999, 0, -10):
            with self.subTest(dz=dz), self.assertRaises(RuntimeError):
                force_completion("withdraw", dz, 30, "LOOSENING", settings, 30)

    def test_tightening_direction_depth_timeout_and_stall(self):
        settings = load_settings()
        self.assertIsNone(force_completion("tighten", -20, 5, "TIGHTENING", settings, 30))
        self.assertEqual(force_completion("tighten", -5, 3, "STALLED", settings, 30), "motor_stall")
        for dz, elapsed, motor in ((-20.001, 5, "TIGHTENING"), (-21, 5, "STALLED"), (-5, 30.1, "TIGHTENING")):
            with self.subTest(dz=dz, elapsed=elapsed), self.assertRaises(RuntimeError):
                force_completion("tighten", dz, elapsed, motor, settings, 30)

    def test_config_validation(self):
        original = MOVEMENT_CONFIG.read_text()
        for old, new in (("speed_deg_s = 10.0", "speed_deg_s = nan"),
                         ("min_accepted_upward_displacement_mm = 10.0", "min_accepted_upward_displacement_mm = 21.0"),
                         ("preserve_payload_during_operation = true", "preserve_payload_during_operation = false")):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "movement.ini"
                path.write_text(original.replace(old, new))
                with self.assertRaises(ValueError):
                    load_settings(path)


def fake_connections(receiver_values=None):
    receiver, control, tool = MagicMock(), MagicMock(), MagicMock()
    receiver.getActualQ.return_value = [0.] * 6
    receiver.getActualQd.return_value = [0.] * 6
    receiver.getActualTCPPose.return_value = [0.] * 6
    receiver.getActualTCPForce.return_value = [0.] * 6
    receiver.getTimestamp.side_effect = range(1000)
    receiver.getRobotMode.return_value = 7
    receiver.getSafetyMode.return_value = 1
    control.getAsyncOperationProgressEx.return_value.isAsyncOperationRunning.return_value = False
    return receiver, control, tool


class StepTests(unittest.TestCase):
    def run_step(self, args, receiver, control, tool, statuses=None, poses=None, **options):
        modules = {"rtde_receive": MagicMock(RTDEReceiveInterface=MagicMock(return_value=receiver)),
                   "rtde_control": MagicMock(RTDEControlInterface=MagicMock(return_value=control))}
        status_patch = (patch.object(step.tool_protocol, "status", side_effect=statuses) if statuses else
                        patch.object(step.tool_protocol, "status",
                                     return_value={"states": ["IDLE", "IDLE"], "pwm_percent": 0}))
        with patch.object(step, "Log"), patch.object(step.time, "sleep"), \
             patch.object(step.time, "monotonic", side_effect=itertools.count(0, .1)), \
             patch("builtins.input", return_value="RUN"), patch.dict(sys.modules, modules), \
             patch.object(step.tool_protocol, "LineClient", return_value=tool), status_patch, \
             patch.object(step, "load_poses", return_value=poses or {"bd_home": [0.] * 6}):
            return step.run(args, **options)

    def test_move_profiles(self):
        for profile, speed, timeout in (("local", 10., 60.), ("cross_home", 20., 120.)):
            with self.subTest(profile=profile):
                receiver, control, tool = fake_connections()
                args = step.parser().parse_args(["move", "--pose", "bd_home", "--profile", profile, "--live"])
                self.assertEqual(self.run_step(args, receiver, control, tool), 0)
                self.assertAlmostEqual(control.moveJ.call_args.args[1], speed * 3.141592653589793 / 180)
                control.setPayload.assert_not_called()
                control.forceMode.assert_not_called()
                control.stopJ.assert_called_once()

    def test_live_flag_is_required(self):
        args = step.parser().parse_args(["move", "--pose", "bd_home"])
        with self.assertRaises(ValueError):
            step.run(args)

    def test_experimental_xy_hold_uses_only_z_compliance(self):
        receiver, control, tool = fake_connections()

        def status(_tool, timeout=None):
            if tool.command.call_args.args[0] == "STOP":
                return {"states": ["IDLE", "IDLE"], "pwm_percent": 0}
            return {"states": ["IDLE", "STALLED"]}

        args = step.parser().parse_args(["tighten", "--motor", "2", "--live"])
        self.assertEqual(self.run_step(args, receiver, control, tool, status, xy_hold=True), 0)
        force_call = control.forceMode.call_args.args
        self.assertEqual(force_call[1], [0, 0, 1, 0, 0, 0])
        self.assertEqual(force_call[4][:3], [.003, .003, .005])
        control.forceModeStop.assert_called_once()
        control.stopScript.assert_called_once()

    def test_experimental_xy_guard_aborts_withdrawal_before_height_can_pass(self):
        receiver, control, tool = fake_connections()
        receiver.getActualTCPPose.side_effect = [[0.] * 6] * 3 + [[.004, 0, .021, 0, 0, 0]] * 100

        def guard(state):
            if abs(state["tcp"][0]) > .003:
                raise RuntimeError("XY limit exceeded")

        args = step.parser().parse_args(["withdraw", "--motor", "2", "--live"])
        self.assertEqual(self.run_step(args, receiver, control, tool, xy_guard=guard), 1)
        tool.command.assert_any_call("LOOSEN M2", "OK LOOSEN M2")
        tool.command.assert_any_call("STOP", "OK STOP")
        control.stopL.assert_called_once()
        control.forceModeStop.assert_called_once()
        control.stopScript.assert_called_once()

    def test_force_actions_stop_on_success_and_error(self):
        for action, states, expected, release_height in (
                ("tighten", ["TIGHTENING", "STALLED"], 0, None), ("tighten", ["IDLE"], 1, None),
                ("withdraw", ["STALLED"], 1, None), ("withdraw", ["LOOSENING"], 0, .015),
                ("withdraw", ["LOOSENING"], 1, .009)):
            with self.subTest(action=action, states=states, height=release_height):
                receiver, control, tool = fake_connections()
                if release_height is not None:
                    receiver.getActualTCPPose.side_effect = [[0.] * 6] * 3 + [[0, 0, release_height, 0, 0, 0]] * 1000
                receiver.getPayload.return_value = 2.110
                motor_states = iter(states)

                def status(_tool, timeout=None):
                    if tool.command.call_args.args[0] == "STOP":
                        return {"states": ["IDLE", "IDLE"], "pwm_percent": 0}
                    return {"states": [next(motor_states, states[-1]), "IDLE"]}

                args = step.parser().parse_args([action, "--motor", "1", "--live"])
                self.assertEqual(self.run_step(args, receiver, control, tool, status), expected)
                tool.command.assert_any_call("STOP", "OK STOP")
                control.stopL.assert_called_once()
                control.stopScript.assert_called_once()
                control.forceModeStop.assert_called_once()
                control.setPayload.assert_not_called()
                order = [call[0] for call in control.mock_calls + tool.mock_calls]
                self.assertLess(order.index("forceMode"), order.index("command"))

    def test_engaged_actions_refuse_payload_changes_and_missing_motor(self):
        for argv in (["tighten", "--motor", "1", "--payload", "tool_only"], ["withdraw"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                step.run(step.parser().parse_args([*argv, "--live"]))


class OperationTests(unittest.TestCase):
    def test_every_zone_recipe(self):
        poses = load_poses()
        for zone in "ABCD":
            for kind, motors, payload, done in (("pick", ("1", "2"), "tool_with_floor", "pickup_completed"),
                                                ("place", ("2", "1"), "tool_only", "placement_completed")):
                steps = worker.operation_steps(kind, zone)
                self.assertEqual([step_[2] for step_ in steps],
                                 [None, None, None, done, "high_clearance_reached", "home_reached"])
                self.assertEqual(steps[2][1][:3], ["tighten", "--motor", motors[0]])
                self.assertEqual(steps[3][1][:3], ["withdraw", "--motor", motors[1]])
                self.assertEqual(steps[4][1][-1], payload)
                self.assertEqual(steps[5][1][2], worker.home_for(zone))
                for _, arguments, _ in steps:
                    if arguments[0] == "move":
                        self.assertIn(arguments[arguments.index("--pose") + 1], poses)

    def test_home_routes_use_distinct_waypoints_and_the_cross_home_profile(self):
        poses = load_poses()
        for destination, prefix in (("ac_home", "bd_ac"), ("bd_home", "ac_bd")):
            steps = worker.operation_steps("travel", destination)
            self.assertEqual([s[1][2] for s in steps], [f"{prefix}_{i}" for i in range(1, 6)] + [destination])
            self.assertTrue(all(s[1][-2:] == ["--profile", "cross_home"] for s in steps))
            self.assertTrue(all(s[1][2] in poses for s in steps))
            self.assertEqual([s[2] for s in steps], [None] * 5 + ["home_reached"])


class FailingRobot(worker.DryRobot):
    def __init__(self, fail_at):
        super().__init__(0)
        self.fail_at, self.calls = fail_at, 0

    def run_step(self, arguments, output):
        self.calls += 1
        return 1 if self.calls == self.fail_at else 0


def run_worker(robot, script, wait=0.3):
    commands, events = queue.Queue(), queue.Queue()
    with tempfile.TemporaryDirectory() as directory:
        instance = worker.Worker(commands, events, robot, Path(directory))
        thread = threading.Thread(target=instance.serve, daemon=True)
        thread.start()
        for message in script:
            commands.put(message)
        time.sleep(wait)
        commands.put(("stop",))
        thread.join(5)
    result = []
    while not events.empty():
        result.append(events.get())
    return result


class WorkerTests(unittest.TestCase):
    def test_detect_then_pick_and_place_emit_ordered_events(self):
        events = run_worker(worker.DryRobot(0), [
            ("detect",), ("operation", 1, "pick", "D", False), ("operation", 2, "travel", "ac_home", True),
            ("operation", 3, "place", "A", True)])
        names = [(e[0], e[1] if e[0] != "detected" else None) for e in events if e[0] != "step"]
        self.assertEqual(names, [("detected", None), ("pickup_completed", 1), ("high_clearance_reached", 1),
                                 ("home_reached", 1), ("home_reached", 2), ("placement_completed", 3),
                                 ("high_clearance_reached", 3), ("home_reached", 3)])
        self.assertEqual(events[0][1], "bd_home")
        self.assertEqual([e for e in events if e[0] == "home_reached"], [
            ("home_reached", 1, "bd_home"), ("home_reached", 2, "ac_home"), ("home_reached", 3, "ac_home")])

    def test_operation_from_the_wrong_home_or_before_detection_faults(self):
        events = run_worker(worker.DryRobot(0), [("detect",), ("operation", 1, "pick", "A", False)])
        self.assertEqual(events[-1][:2], ("fault", 1))
        self.assertIn("requires the robot at ac_home", events[-1][2])
        self.assertEqual(run_worker(worker.DryRobot(0), [("operation", 1, "pick", "B", False)])[-1][0], "fault")

    def test_failed_step_faults_once_and_blocks_later_operations(self):
        events = run_worker(FailingRobot(fail_at=3), [("detect",), ("operation", 1, "pick", "B", False),
                                                      ("operation", 2, "pick", "D", False)])
        faults = [e for e in events if e[0] == "fault"]
        self.assertEqual([f[1] for f in faults], [1, 2])
        self.assertIn("Tighten", faults[0][2])
        self.assertNotIn("pickup_completed", [e[0] for e in events])  # Stopped before the release step.

    def test_pause_waits_for_the_next_step_and_resume_continues(self):
        commands, events = queue.Queue(), queue.Queue()
        with tempfile.TemporaryDirectory() as directory:
            instance = worker.Worker(commands, events, worker.DryRobot(0.05), Path(directory))
            thread = threading.Thread(target=instance.serve, daemon=True)
            thread.start()
            commands.put(("detect",))
            commands.put(("operation", 1, "pick", "B", False))
            time.sleep(0.12)
            commands.put(("pause", True))
            time.sleep(0.3)
            paused_at = events.qsize()
            time.sleep(0.3)
            self.assertEqual(events.qsize(), paused_at)  # Nothing starts while paused.
            self.assertFalse(any(e[0] == "home_reached" for e in list(events.queue)))
            commands.put(("pause", False))
            time.sleep(0.6)
            commands.put(("stop",))
            thread.join(5)
        self.assertTrue(any(e[0] == "home_reached" for e in list(events.queue)))

    def test_jog_ends_when_the_key_is_released_and_detect_sees_the_new_home(self):
        robot = worker.DryRobot(0, start_home=None)
        commands, events = queue.Queue(), queue.Queue()
        with tempfile.TemporaryDirectory() as directory:
            instance = worker.Worker(commands, events, robot, Path(directory))
            thread = threading.Thread(target=instance.serve, daemon=True)
            thread.start()
            commands.put(("detect",))
            time.sleep(0.2)
            for _ in range(4):
                commands.put(("jog", "ac_home"))
                time.sleep(0.1)
            time.sleep(0.8)
            commands.put(("detect",))
            time.sleep(0.2)
            commands.put(("stop",))
            thread.join(5)
        names = [e[0] for e in list(events.queue)]
        self.assertEqual(names, ["detected", "jog_ended", "detected"])
        self.assertEqual([e[1] for e in events.queue if e[0] == "detected"], [None, "ac_home"])


if __name__ == "__main__":
    unittest.main()
