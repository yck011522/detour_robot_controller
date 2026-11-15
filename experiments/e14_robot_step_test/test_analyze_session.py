"""Offline evidence parsing, numerical metrics and relocated/empty-session cases."""

from dataclasses import asdict
import csv
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_session import analyze, orientation_error
from movement_settings import DEFAULT_CONFIG
from queue_cli import read_queue_settings
from transfer_queue import TransferQueue


def event(time, event_name, **data):
    return dict(time=f"2026-11-15T14:00:{time}+08:00", event=event_name, **data)


def write_records(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def initial_event():
    order, _ = read_queue_settings(DEFAULT_CONFIG)
    model = TransferQueue(dict(a="P1", b="P2", c="P3", d=None), order, "bd_home")
    return event("00", "SESSION_STARTED", model=asdict(model))


class AnalyzeSessionTest(unittest.TestCase):
    def test_equivalent_axis_angle_signs_do_not_look_like_rotation_drift(self):
        self.assertAlmostEqual(orientation_error([math.pi, 0, 0], [-math.pi, 0, 0]), 0)

    def test_zero_action_session_produces_empty_csv_without_matplotlib(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            write_records(directory/"events.jsonl", [initial_event()])
            (directory/"robot_pose.ini").write_text("")
            with patch("builtins.print"):
                report = analyze(directory, make_plot=False)
            self.assertEqual(report["actions"], 0)
            self.assertEqual(report["transfers_this_session"], 0)
            self.assertEqual(report["issues"], [])
            with (directory/"review/actions.csv").open(encoding="utf-8") as stream:
                self.assertEqual(list(csv.DictReader(stream)), [])

    def test_relocated_complete_action_has_correct_units_and_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            first = initial_event()
            operation = event("00", "OPERATION_STARTED", model=first["model"], kind="pick", zone="b",
                              transcript="C:\\old\\operation_0001.txt")
            write_records(directory/"events.jsonl", [first, operation])
            (directory/"robot_pose.ini").write_text("")
            (directory/"operation_0001.txt").write_text("Log: C:\\old\\action.log\n")
            state = dict(tcp=[0.]*6, force=[0.]*6, joints=[0.]*6, joint_velocity=[0.]*6,
                         safety_mode=1, robot_mode=7)
            sample = dict(state, tcp=[0,.003,-.005,0,0,0], force=[1,2,3,0,0,0])
            start = event("01", "ACTION_START", settings=dict(action="tighten", motor=1, pose=None),
                          start_z=0., start_tcp=[0.]*6, movement_settings={"joint_motion": {"speed_deg_s": 5}})
            write_records(directory/"action.log", [
                event("00", "STATE", **state), start,
                event("02", "RX", command="STATUS", reply="OK STALLED IDLE 807 0"),
                event("03", "SAMPLE", **sample),
                event("03", "CRITERION_REACHED", reason="motor_stall"),
                event("03.2", "FINAL_STATE", **sample),
                event("03.5", "END", outcome="FINISHED"),
            ])
            with patch("builtins.print"):
                report = analyze(directory, make_plot=False)
            self.assertEqual(report["actions"], 1)
            self.assertEqual(report["issues"], [])
            with (directory/"review/actions.csv").open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(float(rows[0]["duration_s"]), 2.)
            self.assertEqual(float(rows[0]["delta_z_final_mm"]), -5.)
            self.assertEqual(float(rows[0]["max_xy_mm"]), 3.)
            self.assertEqual(float(rows[0]["peak_current_ma"]), 807.)

    def test_missing_action_evidence_is_reported_not_silently_passed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            first = initial_event()
            operation = event("01", "OPERATION_STARTED", model=first["model"], kind="pick", zone="b",
                              transcript=str(directory/"missing.txt"))
            write_records(directory/"events.jsonl", [first, operation])
            (directory/"robot_pose.ini").write_text("")
            with patch("builtins.print"):
                report = analyze(directory, make_plot=False)
            self.assertTrue(report["issues"])
            self.assertEqual(report["actions"], 0)


if __name__ == "__main__":
    unittest.main()
