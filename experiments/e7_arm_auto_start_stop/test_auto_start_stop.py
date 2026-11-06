"""Offline tests for Dashboard startup and rejection handling."""

import unittest
from unittest.mock import patch

import auto_start_stop as experiment


class FakeDashboard:
    def __init__(self, initial_mode, brake_response, modes):
        self.initial_mode = initial_mode
        self.brake_response = brake_response
        self.modes = iter(modes)
        self.commands = []
        self.mode_at_brake_release = None
        self.last_mode = None
        self.robotmode_reads = 0
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def makefile(self, mode):
        return self

    def write(self, data):
        self.commands.append(data.decode("ascii").strip())

    def flush(self):
        pass

    def readline(self):
        if not self.commands:
            return b"Connected: Universal Robots Dashboard Server\n"
        command = self.commands[-1]
        if command == "is in remote control":
            response = "true"
        elif command == "robotmode":
            self.robotmode_reads += 1
            mode = self.initial_mode if self.robotmode_reads == 1 else next(self.modes)
            self.last_mode = mode
            response = f"Robotmode: {mode}"
        elif command == "brake release":
            self.mode_at_brake_release = self.last_mode
            response = self.brake_response
        elif command == "power on":
            response = "Powering on"
        elif command == "stop":
            response = "Stopped"
        elif command == "power off":
            response = "Powering off"
        else:
            raise AssertionError(f"Unexpected command: {command}")
        return (response + "\n").encode("ascii")


class StartupTests(unittest.TestCase):
    def run_dashboard(self, dashboard):
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        with (
            patch.object(experiment.socket, "create_connection", return_value=dashboard),
            patch.object(experiment.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(experiment.time, "sleep", side_effect=sleep),
        ):
            experiment.main()
        return clock[0]

    def test_idle_timeout_does_not_send_brake_release_or_shutdown(self):
        dashboard = FakeDashboard(
            "POWER_OFF", "Brake releasing", ["POWER_ON"] * 15
        )
        with self.assertRaisesRegex(experiment.RobotModeTimeout, "IDLE.*15 seconds"):
            self.run_dashboard(dashboard)
        self.assertEqual(dashboard.commands.count("robotmode"), 16)
        self.assertNotIn("brake release", dashboard.commands)
        self.assertNotIn("stop", dashboard.commands)
        self.assertNotIn("power off", dashboard.commands)

    def test_rejection_after_idle_fails_immediately(self):
        dashboard = FakeDashboard(
            "IDLE", "Unable to brake release, robot mounting is not correct", ["IDLE"] * 3
        )
        with self.assertRaises(experiment.DashboardRejected):
            self.run_dashboard(dashboard)
        self.assertEqual(dashboard.commands[-1], "brake release")
        self.assertNotIn("power off", dashboard.commands)

    def test_already_running_skips_brake_release(self):
        dashboard = FakeDashboard(
            "RUNNING", "", ["RUNNING", "RUNNING", "RUNNING", "POWER_OFF"]
        )
        self.run_dashboard(dashboard)
        self.assertNotIn("brake release", dashboard.commands)
        self.assertIn("power off", dashboard.commands)

    def test_fresh_restart_waits_for_stable_idle_before_brake_release(self):
        dashboard = FakeDashboard(
            "POWER_OFF",
            "Brake releasing",
            [
                "POWER_OFF", "POWER_ON", "BOOTING", "POWER_ON",
                "IDLE", "POWER_ON", "IDLE", "IDLE", "IDLE",
                "BOOTING", "IDLE", "RUNNING", "RUNNING", "RUNNING", "POWER_OFF",
            ],
        )
        self.run_dashboard(dashboard)
        self.assertEqual(dashboard.commands.count("power on"), 1)
        self.assertEqual(dashboard.commands.count("brake release"), 1)
        self.assertEqual(dashboard.mode_at_brake_release, "IDLE")
        brake_index = dashboard.commands.index("brake release")
        self.assertEqual(dashboard.commands[2], "power on")
        self.assertEqual(dashboard.commands[brake_index - 3:brake_index], ["robotmode"] * 3)
        self.assertEqual(dashboard.commands[brake_index + 1:brake_index + 6], ["robotmode"] * 5)
        self.assertIn("power off", dashboard.commands)

    def test_initial_booting_waits_for_idle(self):
        dashboard = FakeDashboard(
            "BOOTING", "Brake releasing",
            ["POWER_ON", "IDLE", "IDLE", "IDLE", "RUNNING", "RUNNING", "RUNNING", "POWER_OFF"],
        )
        self.run_dashboard(dashboard)
        self.assertNotIn("power on", dashboard.commands)
        self.assertEqual(dashboard.mode_at_brake_release, "IDLE")

    def test_running_timeout_does_not_send_shutdown(self):
        dashboard = FakeDashboard(
            "IDLE", "Brake releasing", ["IDLE"] * 3 + ["IDLE"] * 60
        )
        with self.assertRaisesRegex(experiment.RobotModeTimeout, "RUNNING.*60 seconds"):
            self.run_dashboard(dashboard)
        self.assertNotIn("stop", dashboard.commands)
        self.assertNotIn("power off", dashboard.commands)

    def test_power_off_requires_only_one_report(self):
        dashboard = FakeDashboard(
            "RUNNING", "", ["RUNNING"] * 3 + ["POWER_OFF"]
        )
        self.run_dashboard(dashboard)
        self.assertEqual(dashboard.commands[-3:], ["stop", "power off", "robotmode"])


if __name__ == "__main__":
    unittest.main()
