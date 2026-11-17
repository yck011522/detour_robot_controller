"""Real UR10e adapter: automatic arm power, pose detection, jogging and step execution."""

import math
from pathlib import Path
import subprocess
import sys
import time

from . import tool as tool_protocol
from .settings import MOVEMENT_CONFIG, ROOT, load_payload, load_poses, load_settings
from .step import settled_matches, snapshot
from .power import ArmPower

HOMES = ("ac_home", "bd_home")
HOME_TOLERANCE_DEG = 5.0  # Startup recognition only; movement arrival keeps its own tolerance.


class LiveRobot:
    def __init__(self, config_path=MOVEMENT_CONFIG):
        self.config_path = Path(config_path)
        self.settings = load_settings(self.config_path)
        self.poses = load_poses()
        self.power = ArmPower()

    def shutdown(self):
        self.power.stop()

    def receiver(self):
        from rtde_receive import RTDEReceiveInterface
        return RTDEReceiveInterface(tool_protocol.host_from_config(), self.settings["runtime"]["receive_hz"])

    def fresh_state(self, receiver):
        first = receiver.getTimestamp()
        time.sleep(self.settings["joint_motion"]["settle_seconds"])
        state = snapshot(receiver)
        if state["timestamp"] <= first:
            raise RuntimeError("Robot feedback is not updating")
        if state["safety_mode"] != 1 or state["robot_mode"] != 7:
            raise RuntimeError("Robot is not in NORMAL safety / RUNNING mode")
        if max(map(abs, state["joint_velocity"])) > self.settings["joint_motion"]["stationary_velocity_rad_s"]:
            raise RuntimeError("Robot must be stationary")
        return state

    def detect(self):
        """Return (home or None, description). Connection or state problems raise."""
        self.power.start()
        receiver = self.receiver()
        try:
            state = self.fresh_state(receiver)
        finally:
            receiver.disconnect()
        errors = settled_matches(state["joints"], {home: self.poses[home] for home in HOMES})
        home, error = min(errors.items(), key=lambda item: item[1])
        summary = ", ".join(f"{name} {value:.1f} deg" for name, value in errors.items())
        if error <= HOME_TOLERANCE_DEG:
            return home, f"At {home} ({summary})"
        return None, f"Not at a home ({summary})"

    def check_start(self, pose, payload, tolerance=None):
        """Recognize the starting endpoint; never infer a route from an arbitrary pose."""
        mass, cog = load_payload(payload)
        receiver = self.receiver()
        try:
            state = self.fresh_state(receiver)
            error = settled_matches(state["joints"], {pose: self.poses[pose]})[pose]
            limit = tolerance or self.settings["joint_motion"]["arrival_tolerance_deg"]
            if error > limit:
                raise RuntimeError(f"Start requires {pose}; maximum error {error:.3f} deg")
            runtime = self.settings["runtime"]
            actual_mass, actual_cog = receiver.getPayload(), list(receiver.getPayloadCog())
            if not math.isfinite(actual_mass) or abs(actual_mass - mass) > runtime["payload_mass_tolerance_kg"]:
                raise RuntimeError(f"Start requires the configured {payload} payload")
            if len(actual_cog) != 3 or any(not math.isfinite(a) or abs(a - b) > runtime["payload_cog_tolerance_m"]
                                           for a, b in zip(actual_cog, cog)):
                raise RuntimeError("Starting payload CoG differs from the selected payload")
        finally:
            receiver.disconnect()

    def run_step(self, arguments, output):
        """Return the child's exit code. The child is isolated from console Ctrl+C."""
        child = subprocess.run(
            [sys.executable, "-m", "game_controller.robot.step", *arguments,
             "--config", str(self.config_path), "--live"],
            input="RUN\n", text=True, stdout=output, stderr=subprocess.STDOUT, cwd=ROOT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return child.returncode

    def dwell(self):
        time.sleep(self.settings["joint_motion"]["step_dwell_seconds"])

    def jog(self, pose, keep_going):
        """Move toward a taught pose only while keep_going() stays true."""
        from rtde_control import RTDEControlInterface
        joint, runtime = self.settings["joint_motion"], self.settings["runtime"]
        receiver = self.receiver()
        control = None
        try:
            self.fresh_state(receiver)
            control = RTDEControlInterface(tool_protocol.host_from_config())
            if not control.setWatchdog(runtime["watchdog_min_hz"]):
                raise RuntimeError("Watchdog setup failed")
            if not control.moveJ(self.poses[pose], math.radians(joint["speed_deg_s"]),
                                 math.radians(joint["acceleration_deg_s2"]), True):
                raise RuntimeError("MoveJ rejected")
            while keep_going():
                if not control.kickWatchdog():
                    raise RuntimeError("Watchdog update failed")
                if not control.getAsyncOperationProgressEx().isAsyncOperationRunning():
                    break
                time.sleep(runtime["loop_sleep_seconds"])
        finally:
            if control:
                try:
                    control.stopJ(runtime["stop_joint_deceleration_rad_s2"])
                    control.stopScript()
                finally:
                    control.disconnect()
            receiver.disconnect()
