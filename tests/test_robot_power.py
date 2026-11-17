"""Offline coverage of the E7-derived game arm power lifecycle."""

import queue
import unittest
from unittest.mock import Mock, patch

from game_controller.robot import power, worker
from game_controller.robot.live import LiveRobot


class PowerTests(unittest.TestCase):
    def client(self, mode):
        client = Mock()
        client.command.side_effect = lambda command, *a, **kw: f"Robotmode: {mode}" if command == "robotmode" else "OK"
        return client

    def test_startup_sequences_from_supported_modes(self):
        for mode in ("POWER_OFF", "POWER_ON", "BOOTING", "IDLE", "RUNNING"):
            with self.subTest(mode=mode):
                client, sequence = self.client(mode), []
                original = client.command.side_effect
                client.command.side_effect = lambda command, *a, **kw: sequence.append(command) or original(command)
                with patch.object(power, "dashboard") as connect, \
                     patch.object(power, "wait_mode", side_effect=lambda c, m, *a, **kw: sequence.append(m)):
                    connect.return_value.__enter__.return_value = client
                    arm = power.ArmPower()
                    arm.start()
                    arm.start()  # Home rechecks must not repeat power/brake commands.
                expected = ["is in remote control", "safetystatus", "running", "robotmode"]
                if mode == "POWER_OFF":
                    expected += ["power on"]
                if mode != "RUNNING":
                    expected += ["IDLE", "brake release"]
                self.assertEqual(sequence, expected + ["RUNNING"])
                self.assertTrue(arm.ready)

    def test_idle_timeout_never_releases_brakes_but_quit_can_power_off(self):
        client = self.client("POWER_OFF")
        with patch.object(power, "dashboard") as connect, patch.object(power, "wait_mode", side_effect=TimeoutError):
            connect.return_value.__enter__.return_value = client
            arm = power.ArmPower()
            with self.assertRaises(TimeoutError):
                arm.start()
        self.assertTrue(arm.managed)
        self.assertNotIn("brake release", [c.args[0] for c in client.command.call_args_list])
        self.assertFalse(arm.ready)

    def test_shutdown_attempts_power_off_even_when_stop_fails(self):
        client = Mock()
        client.command.side_effect = [RuntimeError("stop rejected"), "Powering off"]
        arm = power.ArmPower()
        arm.managed = True
        with patch.object(power, "dashboard") as connect, patch.object(power, "wait_mode") as wait:
            connect.return_value.__enter__.return_value = client
            with self.assertRaisesRegex(RuntimeError, "stop rejected"):
                arm.stop()
        self.assertEqual([c.args[0] for c in client.command.call_args_list], ["stop", "power off"])
        wait.assert_called_once_with(client, "POWER_OFF", 60, polls=1)

    def test_refused_preflight_does_not_take_over_arm(self):
        client = Mock()
        client.command.side_effect = RuntimeError("not remote")
        with patch.object(power, "dashboard") as connect:
            connect.return_value.__enter__.return_value = client
            arm = power.ArmPower()
            with self.assertRaises(RuntimeError):
                arm.start()
            arm.stop()
            self.assertEqual(connect.call_count, 1)

    def test_shutdown_timeout_propagates_and_never_shuts_down_controller(self):
        client = self.client("RUNNING")
        arm = power.ArmPower()
        arm.managed = True
        with patch.object(power, "dashboard") as connect, patch.object(power, "wait_mode", side_effect=TimeoutError):
            connect.return_value.__enter__.return_value = client
            with self.assertRaises(TimeoutError):
                arm.stop()
        self.assertEqual([c.args[0] for c in client.command.call_args_list], ["stop", "power off"])

    def test_worker_always_shuts_down_after_serve_failure(self):
        robot = Mock()
        with patch.object(worker.signal, "signal"), patch.object(worker, "DryRobot", return_value=robot), \
             patch.object(worker.Worker, "serve", side_effect=RuntimeError("worker failed")):
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                worker.run(queue.Queue(), queue.Queue(), mode="dry-run")
        robot.shutdown.assert_called_once_with()

    def test_detect_powers_up_before_reading_feedback(self):
        robot = LiveRobot()
        sequence = []
        robot.power = Mock()
        robot.power.start.side_effect = lambda: sequence.append("power")

        def offline():
            sequence.append("feedback")
            raise RuntimeError("offline")

        robot.receiver = Mock(side_effect=offline)
        with self.assertRaisesRegex(RuntimeError, "offline"):
            robot.detect()
        self.assertEqual(sequence, ["power", "feedback"])


if __name__ == "__main__":
    unittest.main()
