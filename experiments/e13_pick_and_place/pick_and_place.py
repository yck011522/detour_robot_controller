"""E13 supervised pick/place. Default preview is offline; --live enables Connect.

One child process owns all robot/tool I/O so a native call cannot freeze the UI.
No startup power/brake operations. Every action needs a fresh held mouse press.
"""
import argparse
import configparser
from dataclasses import dataclass
from datetime import datetime
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "e12_robot_teaching"))
sys.path.insert(0, str(HERE.parent / "e10_tool_rs485"))
import hold_to_run as e12
import tool_test as e10

# Experiment-local settings, displayed before enabling movement.
FORCE_N = 10.0
POSITION_TOLERANCE_M = .0005
MAX_XY_DRIFT_M = .010
CHECK_XY_DRIFT = False  # Temporary hand-held fixture test; restore for fixed fixture.
MAX_Z_OVERTRAVEL_M = .010
FORCE_SPEED_M_S = .005
HEARTBEAT_LIMIT_S = .25
TOOL_REPLY_LIMIT_S = .12
PERIOD_S = .02


@dataclass(frozen=True)
class Action:
    label: str
    destination: int
    kind: str
    point: str
    motor: int = 0
    payload_after: str = ""


# States describe physical endpoints, not a command history to replay blindly.
FORWARD = {
    0: Action("Carry floor: HOME -> HIGH", 1, "J", "HIGH"),
    1: Action("Carry floor: HIGH -> LOW", 2, "J", "LOW"),
    2: Action("Place: descend + tighten M2", 3, "DOWN", "TARGET", 2, "tool_only"),
    3: Action("Release floor: loosen M1 + withdraw", 4, "UP", "LOW", 1),
    4: Action("Empty tool: LOW -> HIGH", 5, "J", "HIGH"),
    5: Action("Empty tool: HIGH -> HOME", 6, "J", "HOME"),
    6: Action("Pickup approach: HOME -> HIGH", 7, "J", "HIGH"),
    7: Action("Pickup approach: HIGH -> LOW", 8, "J", "LOW"),
    8: Action("Pickup: descend + tighten M1", 9, "DOWN", "TARGET", 1, "tool_with_floor"),
    9: Action("Uninstall: loosen M2 + withdraw", 10, "UP", "LOW", 2),
    10: Action("Carry floor: LOW -> HIGH", 11, "J", "HIGH"),
    11: Action("Carry floor: HIGH -> HOME", 12, "J", "HOME"),
}
REVERSE = {
    1: Action("Return carrying floor to HOME", 0, "J", "HOME"),
    2: Action("Return carrying floor to HIGH", 1, "J", "HIGH"),
    3: Action("Undo installation: loosen M2 + withdraw", 2, "UP", "LOW", 2),
    4: Action("Regrip installed floor: descend + tighten M1", 3, "DOWN", "TARGET", 1, "tool_with_floor"),
    5: Action("Return empty tool to LOW", 4, "J", "LOW"),
    6: Action("Return empty tool to HIGH", 5, "J", "HIGH"),
    7: Action("Return empty tool to HOME", 6, "J", "HOME"),
    8: Action("Return empty tool to HIGH", 7, "J", "HIGH"),
    9: Action("Undo pickup grip: loosen M1 + withdraw", 8, "UP", "LOW", 1),
    10: Action("Reinstall floor: descend + tighten M2", 9, "DOWN", "TARGET", 2, "tool_only"),
    11: Action("Return carrying floor to LOW", 10, "J", "LOW"),
    12: Action("Return carrying floor to HIGH", 11, "J", "HIGH"),
}
STATE_POINTS = ("HOME", "HIGH", "LOW", "TARGET", "LOW", "HIGH", "HOME",
                "HIGH", "LOW", "TARGET", "LOW", "HIGH", "HOME")


class EventLog:
    def __init__(self):
        (HERE / "results").mkdir(exist_ok=True)
        self.file = (HERE / "results" / f"{datetime.now():%Y%m%d_%H%M%S_%f}.log").open("x", encoding="utf-8")

    def write(self, event, **data):
        self.file.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(), event=event, **data)) + "\n")
        self.file.flush()


class Hardware:
    """Direct ur_rtde calls plus E10's already tested ASCII tool protocol."""
    def __init__(self, log, report):
        self.log, self.report = log, report
        self.control = self.receiver = self.tool = None
        self.force_active = False
        self.motion_kind = "J"

    def connect(self):
        from rtde_control import RTDEControlInterface
        from rtde_receive import RTDEReceiveInterface
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "config/address.ini")
        host = config["robotic_arm"]["host"]
        dashboard = e10.dashboard(host, self.log)
        try:
            for command, expected in (("is in remote control", "true"),
                                      ("robotmode", "Robotmode: RUNNING"),
                                      ("safetystatus", "Safetystatus: NORMAL"),
                                      ("running", "Program running: false")):
                dashboard.command(command, expected)
        finally:
            dashboard.close()
        self.receiver = RTDEReceiveInterface(host, 50.0)
        qd = list(self.receiver.getActualQd())
        if not e12.finite_six(qd) or max(map(abs, qd)) > .01:
            raise RuntimeError("Robot must be stationary")
        self.report("Uploading control script; no movement")
        self.control = RTDEControlInterface(host)
        self.tool = e10.LineClient(host, 54321, self.log, timeout=TOOL_REPLY_LIMIT_S)
        self.tool.command("PING", "OK PONG")
        self.stop_tool()
        self.joints = dict(HOME=e12.HOME[:], HIGH=e12.HIGH[:], TARGET=e12.TARGET[:])
        tcp = list(self.control.getTCPOffset())
        if not any(tcp):
            tcp[0] = 1e-12  # E12-tested workaround for the zero-TCP FK recipe.
        self.poses = {}
        for name, q in self.joints.items():
            self.report("Checking FK: " + name)
            pose = list(self.control.getForwardKinematics(q, tcp))
            if not e12.finite_six(pose) or not e12.pose_close(pose, e12.TAUGHT_TCP[name], .001, math.radians(1)):
                raise RuntimeError("Taught TCP mismatch: " + name)
            self.poses[name] = pose
        self.poses["LOW"] = self.poses["TARGET"][:]
        self.poses["LOW"][2] += e12.LOW_OFFSET_M
        if not self.control.getInverseKinematicsHasSolution(self.poses["LOW"], e12.TARGET):
            raise RuntimeError("LOW has no IK solution")
        self.joints["LOW"] = list(self.control.getInverseKinematics(self.poses["LOW"], e12.TARGET))
        if not e12.finite_six(self.joints["LOW"]) or max(abs(a-b) for a,b in zip(self.joints["LOW"], e12.TARGET)) > math.radians(15):
            raise RuntimeError("LOW IK branch mismatch")
        for name in self.joints:
            if not self.control.isJointsWithinSafetyLimits(self.joints[name]) or not self.control.isPoseWithinSafetyLimits(self.poses[name]):
                raise RuntimeError("Controller limit check failed: " + name)
        actual = list(self.receiver.getActualQ())
        if not e12.finite_six(actual):
            raise RuntimeError("Invalid initial joint feedback")
        self.at_home = max(abs(a-b) for a,b in zip(actual, e12.HOME)) <= e12.JOINT_TOLERANCE
        config.read(ROOT / "config/game_config.ini")
        self.payloads = {}
        for name in ("tool_only", "tool_with_floor"):
            section = config["robot_payload_" + name]
            mass = section.getfloat("mass_kg")
            cog = [section.getfloat("cog_" + axis + "_m") for axis in "xyz"]
            if not math.isfinite(mass) or mass <= 0 or not all(map(math.isfinite, cog)):
                raise RuntimeError("Invalid payload calibration")
            self.payloads[name] = (mass, cog)
        self.payload("tool_with_floor")
        # HOME must be free of external contact; tare only after correct gravity
        # compensation is installed, never while pressing on the structure.
        self.control.zeroFtSensor()
        time.sleep(.2)
        self.log.write("FT_ZEROED_AT_HOME")
        if not self.control.setWatchdog(3.0):
            raise RuntimeError("Cannot enable robot watchdog")
        self.last_timestamp = self.receiver.getTimestamp()
        self.fresh_at = time.monotonic()
        self.log.write("PREPARED", poses=self.poses, joints=self.joints,
                       force_n=FORCE_N, force_speed_m_s=FORCE_SPEED_M_S,
                       low_offset_m=e12.LOW_OFFSET_M, payloads=self.payloads,
                       force_task_frame=[0]*6, force_type=2, selection_vector=[1,1,1,0,0,0],
                       position_tolerance_m=POSITION_TOLERANCE_M,
                       overtravel_margin_m=MAX_Z_OVERTRAVEL_M, xy_check_enabled=CHECK_XY_DRIFT,
                       max_xy_drift_m=MAX_XY_DRIFT_M, orientation_limit_deg=3.0,
                       joint_tolerance_rad=e12.JOINT_TOLERANCE,
                       heartbeat_limit_s=HEARTBEAT_LIMIT_S, feedback_limit_s=.2,
                       tool_reply_limit_s=TOOL_REPLY_LIMIT_S, watchdog_min_hz=3.0)

    def payload(self, name):
        # Capture contact/load context: changing payload can alter the FT baseline.
        if self.receiver:
            self.log.write("PAYLOAD_BEFORE", name=name, tcp_force=list(self.receiver.getActualTCPForce()),
                           tcp_pose=list(self.receiver.getActualTCPPose()),
                           controller_timestamp=self.receiver.getTimestamp())
        mass, cog = self.payloads[name]
        if not self.control.setPayload(mass, cog):
            raise RuntimeError("Payload command rejected")
        self.log.write("PAYLOAD", name=name, mass=mass, cog=cog)

    def stop_tool(self):
        self.tool.command("STOP", "OK STOP")
        sample = e10.status(self.tool)
        if sample["states"] != ["IDLE", "IDLE"] or sample["pwm_percent"] != 0:
            raise RuntimeError("Tool STOP unconfirmed")

    def maintain_watchdog(self):
        """Fault UI still owns a connected script; keep its communication alive."""
        if self.control and self.control.isProgramRunning():
            if not self.control.kickWatchdog():
                raise RuntimeError("Watchdog failed")

    def check(self):
        if self.receiver.getSafetyMode() != 1 or self.receiver.getRobotMode() != 7 or not self.control.isProgramRunning():
            raise RuntimeError("Robot mode/safety/program changed")
        if not self.control.kickWatchdog():
            raise RuntimeError("Watchdog failed")
        stamp = self.receiver.getTimestamp()
        if stamp != self.last_timestamp:
            self.last_timestamp, self.fresh_at = stamp, time.monotonic()
        if time.monotonic() - self.fresh_at > .2:
            raise RuntimeError("Robot feedback stale")
        for values in (self.receiver.getActualQ(), self.receiver.getActualQd(), self.receiver.getActualTCPPose()):
            if not e12.finite_six(values):
                raise RuntimeError("Invalid robot feedback")

    def log_fault_snapshot(self, operation=None):
        """Read-only best effort; a failed measurement must not prevent STOP."""
        data = {}
        if operation:
            data.update(action=operation.action.label, kind=operation.action.kind,
                        start_pose=operation.start_pose,
                        target_pose=self.poses.get(operation.action.point),
                        target_joints=self.joints.get(operation.action.point),
                        stalled=operation.stalled, motor_state=operation.motor_state)
        if self.receiver:
            for name, method in (("tcp_pose", "getActualTCPPose"), ("tcp_force", "getActualTCPForce"),
                                 ("joints", "getActualQ"), ("joint_velocity", "getActualQd"),
                                 ("tcp_speed", "getActualTCPSpeed"), ("timestamp", "getTimestamp"),
                                 ("safety_mode", "getSafetyMode"), ("robot_mode", "getRobotMode"),
                                 ("payload_kg", "getPayload"), ("payload_cog_m", "getPayloadCog")):
                try:
                    data[name] = getattr(self.receiver, method)()
                except Exception as error:
                    data[name + "_error"] = str(error)
        self.log.write("FAULT_SNAPSHOT", **data)

    def halt(self):
        # Try both stops even if either communication path fails.
        errors = []
        if self.tool:
            try:
                self.stop_tool()
            except Exception as error:
                errors.append(str(error))
        if self.control:
            # Cancel/join the async Z-hold move before forceModeStop: that
            # command itself executes stopl in the script's main thread.
            # Velocity alone cannot prove that another thread released control.
            try:
                if self.motion_kind == "J":
                    self.control.stopJ(e12.STOP_J_DECELERATION, False)
                else:
                    self.control.stopL(e12.STOP_L_DECELERATION, False)
            except Exception as error:
                errors.append(str(error))
            if self.force_active:
                try:
                    if not self.control.forceModeStop():
                        raise RuntimeError("forceModeStop rejected")
                    self.force_active = False
                except Exception as error:
                    errors.append(str(error))
            try:
                deadline, settled = time.monotonic() + 3, 0
                while settled < 3:
                    # A stopped control script is not evidence that the robot
                    # is moving. Confirm stop from fresh finite velocity data.
                    if self.control.isProgramRunning():
                        self.control.kickWatchdog()
                    stamp = self.receiver.getTimestamp()
                    if stamp != self.last_timestamp:
                        self.last_timestamp, self.fresh_at = stamp, time.monotonic()
                    qd = list(self.receiver.getActualQd())
                    if not e12.finite_six(qd) or time.monotonic() - self.fresh_at > .2:
                        raise RuntimeError("Cannot confirm stop from stale/invalid feedback")
                    settled = settled + 1 if max(map(abs, qd)) < .005 else 0
                    self.log.write("STOP_SAMPLE", joint_velocity=qd, stationary_samples=settled,
                                   required_samples=3, velocity_limit_rad_s=.005,
                                   remaining_s=max(0,deadline-time.monotonic()), deadline_s=3)
                    self.report(f"Stopping: joint speed {max(map(abs,qd)):.5f} / <0.00500 rad/s; "
                                f"stationary samples {settled}/3; remaining {max(0,deadline-time.monotonic()):.2f}/3 s")
                    if time.monotonic() > deadline:
                        raise RuntimeError("Robot stop unconfirmed")
                    time.sleep(PERIOD_S)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("STOP UNCONFIRMED: " + "; ".join(errors))

    def recover(self):
        """Operator-requested recovery; never reset a protective stop or move."""
        self.stop_tool()
        previous = self.receiver.getTimestamp()
        deadline, samples = time.monotonic() + 2, 0
        while samples < 3:
            self.maintain_watchdog()
            stamp = self.receiver.getTimestamp()
            qd = list(self.receiver.getActualQd())
            if not e12.finite_six(qd) or max(map(abs, qd)) >= .005:
                raise RuntimeError("Robot is still moving; recovery not ready")
            if stamp != previous:
                samples += 1
                previous = stamp
            self.log.write("RECOVERY_SAMPLE", joint_velocity=qd, fresh_stationary_samples=samples,
                           required_samples=3, velocity_limit_rad_s=.005,
                           remaining_s=max(0,deadline-time.monotonic()), deadline_s=2)
            self.report(f"Recovery: joint speed {max(map(abs,qd)):.5f} / <0.00500 rad/s; "
                        f"fresh stationary samples {samples}/3; remaining {max(0,deadline-time.monotonic()):.2f}/2 s")
            if time.monotonic() > deadline:
                raise RuntimeError("Robot feedback is not updating")
            time.sleep(PERIOD_S)
        if self.receiver.getRobotMode() != 7 or self.receiver.getSafetyMode() != 1:
            raise RuntimeError("Restore NORMAL safety/RUNNING on the pendant first")
        if not self.control.isProgramRunning():
            if not self.control.reuploadScript():
                raise RuntimeError("Control script restart failed")
        # Confirm all motion/force commands stopped before clearing the fault.
        self.halt()
        if not self.control.setWatchdog(3.0):
            raise RuntimeError("Cannot restore watchdog")
        self.log.write("OPERATOR_RECOVERY_VERIFIED")

    def close(self):
        if self.control:
            try:
                self.control.stopScript()
            finally:
                self.control.disconnect()
        if self.receiver:
            self.receiver.disconnect()
        if self.tool:
            self.tool.close()


class Operation:
    """A paused operation retains its start limits and observed stall."""
    def __init__(self, action, hw):
        self.action, self.hw = action, hw
        self.start_pose = list(hw.receiver.getActualTCPPose())
        self.stalled = False
        self.last_poll = 0.0
        self.last_debug = 0.0
        self.motor_state = "not sampled"
        self.motor_current = self.motor_pwm = None
        self.debug = ""
        self.running = False

    def start(self):
        h, a = self.hw, self.action
        h.motion_kind = "J" if a.kind in ("J", "INITIAL") else "L"
        if a.kind == "UP" and a.motor:
            h.payload("tool_only" if a.motor == 1 else "tool_with_floor")
        if a.kind != "J":
            self.hw.log.write("ACTION_BASELINE", action=a.label,
                              tcp_pose=list(h.receiver.getActualTCPPose()),
                              tcp_force=list(h.receiver.getActualTCPForce()),
                              start_pose=self.start_pose, target_pose=h.poses[a.point],
                              controller_timestamp=h.receiver.getTimestamp())
        if a.motor and not self.stalled:
            command = f"{'LOOSEN' if a.kind == 'UP' else 'TIGHTEN'} M{a.motor}"
            h.tool.command(command, "OK " + command)
        if a.kind == "J":
            if not h.control.moveJ(h.joints[a.point], e12.JOINT_SPEED, e12.JOINT_ACCELERATION, True):
                raise RuntimeError("moveJ rejected")
        # Mark before any force calls so failure cleanup knows a force may exist.
        self.running = True
        h.log.write("ACTION_START", action=a.label, stalled=self.stalled)

    def force(self):
        h = self.hw
        h.force_active = True
        z_force = FORCE_N if self.action.kind == "UP" else -FORCE_N
        if not h.control.forceMode([0]*6, [1,1,1,0,0,0], [0,0,z_force,0,0,0], 2,
                                   [FORCE_SPEED_M_S]*3 + [math.radians(2)]*3):
            raise RuntimeError("forceMode rejected")

    def diagnostics(self, pose, now, force=False):
        if not force and now - self.last_debug < .2:
            return
        h, a = self.hw, self.action
        target_z = h.poses[a.point][2]
        wrench = list(h.receiver.getActualTCPForce())
        requested_fz = 0 if a.kind == "INITIAL" else FORCE_N if a.kind == "UP" else -FORCE_N
        error_mm = (pose[2] - target_z) * 1000
        lower = min(self.start_pose[2], target_z) - MAX_Z_OVERTRAVEL_M
        upper = max(self.start_pose[2], target_z) + MAX_Z_OVERTRAVEL_M
        angle = math.degrees(e12.orientation_distance(pose[3:], self.start_pose[3:]))
        xy = math.dist(pose[:2], self.start_pose[:2])*1000
        displacement = [(v-start)*1000 for v,start in zip(pose[:3],self.start_pose[:3])]
        tcp_speed = list(h.receiver.getActualTCPSpeed())
        lateral_force = math.hypot(wrench[0], wrench[1])
        completion = (f"wait M{a.motor} STALLED (Z is reference only)" if a.kind == "DOWN" and a.motor else
                      f"wait Z error <= {POSITION_TOLERANCE_M*1000:.1f} mm")
        lines = [f"{completion} | stall latched {int(self.stalled)} / required 1",
                 f"Z {pose[2]*1000:.2f} / reference {target_z*1000:.2f} mm; error {error_mm:+.2f} mm",
                 f"Z allowed [{lower*1000:.2f}, {upper*1000:.2f}] mm; orientation {angle:.2f} / max 3 deg",
                 f"XY drift {xy:.2f} / max {MAX_XY_DRIFT_M*1000:.1f} mm ({'ON' if CHECK_XY_DRIFT else 'DISABLED'})",
                 f"Fz request {requested_fz:+.1f} / feedback {wrench[2]:+.2f} N; speed cap {FORCE_SPEED_M_S*1000:.1f} mm/s",
                 f"M{a.motor} {self.motor_state}; current {self.motor_current} mA; PWM {self.motor_pwm} %"]
        if a.kind == "INITIAL":
            drift = math.dist(pose[:3], self.start_pose[:3])*1000
            lines[0] = f"wait M{a.motor} STALLED: latched {int(self.stalled)} / required 1"
            lines.append(f"Initial position drift {drift:.3f} / max 1.000 mm; rotation {angle:.3f} / max 1.000 deg")
        lines.append(f"Fx/Fy {wrench[0]:+.2f}/{wrench[1]:+.2f} N (request 0/0); "
                     f"dX/dY {displacement[0]:+.2f}/{displacement[1]:+.2f} mm")
        self.debug = "\n".join(lines)
        h.log.write("OPERATION_SAMPLE", action=a.label, tcp_pose=pose, tcp_force=wrench,
                    motor_current_ma=self.motor_current, motor_pwm_percent=self.motor_pwm,
                    controller_timestamp=h.receiver.getTimestamp(), tcp_speed=tcp_speed,
                    displacement_mm=displacement, lateral_force_n=lateral_force,
                    requested_wrench=[0,0,requested_fz,0,0,0],
                    force_task_frame=[0]*6, force_type=2, selection_vector=[1,1,1,0,0,0],
                    position_tolerance_m=POSITION_TOLERANCE_M, max_xy_drift_m=MAX_XY_DRIFT_M,
                    xy_check_enabled=CHECK_XY_DRIFT, orientation_limit_deg=3.0,
                    force_speed_limit_m_s=FORCE_SPEED_M_S, overtravel_margin_m=MAX_Z_OVERTRAVEL_M,
                    target_z=target_z, z_error_mm=error_mm, requested_fz=requested_fz,
                    motor_state=self.motor_state, stalled=self.stalled,
                    lower_z=lower, upper_z=upper, orientation_drift_deg=angle, xy_drift_mm=xy)
        self.last_debug = now

    def tick(self):
        h, a = self.hw, self.action
        now = time.monotonic()
        pose = list(h.receiver.getActualTCPPose())
        if a.kind == "J":
            joints = list(h.receiver.getActualQ())
            errors = [abs(x-y) for x,y in zip(joints, h.joints[a.point])]
            moving = h.control.getAsyncOperationProgressEx().isAsyncOperationRunning()
            velocity = max(map(abs, h.receiver.getActualQd()))
            if now - self.last_debug >= .2:
                self.debug = (f"Joint max error {math.degrees(max(errors)):.3f} / max {math.degrees(e12.JOINT_TOLERANCE):.3f} deg; "
                              f"speed {velocity:.5f} / required <0.00500 rad/s; async running {int(moving)} / required 0\n"
                              "Joints actual deg: " + ", ".join(f"{math.degrees(x):.2f}" for x in joints) + "\n"
                              "Joints target deg: " + ", ".join(f"{math.degrees(x):.2f}" for x in h.joints[a.point]))
                h.log.write("JOINT_SAMPLE", action=a.label, joints=joints, target=h.joints[a.point],
                            max_error_rad=max(errors), max_velocity_rad_s=velocity, async_running=moving,
                            joint_tolerance_rad=e12.JOINT_TOLERANCE, velocity_limit_rad_s=.005)
                self.last_debug = now
            return max(errors) <= e12.JOINT_TOLERANCE and not moving and velocity < .005
        target = h.poses[a.point]
        self.diagnostics(pose, now)
        if CHECK_XY_DRIFT and math.dist(pose[:2], self.start_pose[:2]) > MAX_XY_DRIFT_M:
            raise RuntimeError("XY travel bound exceeded")
        lower = min(self.start_pose[2], target[2]) - MAX_Z_OVERTRAVEL_M
        upper = max(self.start_pose[2], target[2]) + MAX_Z_OVERTRAVEL_M
        if not lower <= pose[2] <= upper:
            h.log.write("Z_OVERTRAVEL", action=a.label, actual_z=pose[2],
                        start_z=self.start_pose[2], target_z=target[2],
                        lower_z=lower, upper_z=upper, tcp_pose=pose,
                        tcp_force=list(h.receiver.getActualTCPForce()))
            raise RuntimeError(f"Z overtravel: actual {pose[2]*1000:.3f} mm, "
                               f"allowed [{lower*1000:.3f}, {upper*1000:.3f}] mm")
        if e12.orientation_distance(pose[3:], self.start_pose[3:]) > math.radians(3):
            raise RuntimeError("Orientation drift")
        reached = abs(pose[2] - target[2]) <= POSITION_TOLERANCE_M
        if a.motor and not self.stalled and now - self.last_poll >= .1:
            sample = e10.status(h.tool)
            self.last_poll = now
            h.log.write("MOTOR_STATUS", **sample)
            own, other = sample["states"][a.motor-1], sample["states"][2-a.motor]
            self.motor_state = own
            self.motor_current, self.motor_pwm = sample.get("current_ma"), sample.get("pwm_percent")
            if other != "IDLE":
                raise RuntimeError("Other motor unexpectedly active")
            if own == "STALLED":
                if a.kind == "UP":
                    raise RuntimeError("Loosening motor stalled before withdrawal completed")
                self.stalled = True
                h.log.write("STALL_LATCHED", motor=a.motor)
                h.stop_tool()
            elif own != ("LOOSENING" if a.kind == "UP" else "TIGHTENING"):
                raise RuntimeError("Unexpected motor state: " + own)
        if a.kind == "INITIAL":
            if not e12.pose_close(pose, self.start_pose, .001, math.radians(1)):
                raise RuntimeError("Robot drifted during initial grip confirmation")
            return self.stalled
        # Tightening completion depends on the motor stall, not TARGET Z.
        # Keep Z compliant throughout; no positional moveL or hold transition.
        done = self.stalled if a.kind == "DOWN" and a.motor else reached
        self.diagnostics(pose, now, force=done)
        if not done:
            self.force()
        return done



def worker(commands, events, live):
    """Child-process command owner. Preview never instantiates Hardware."""
    log = EventLog()
    hw = None
    state, operation, active_direction = -1, None, 0
    fresh_at, held, enabled = time.monotonic(), 0, False
    need_release, connected, fault = True, False, False
    def report(message, color="idle"):
        events.put(dict(message=message, color=color, state=state))
    try:
        report("Preview: no hardware connections" if not live else "Disconnected. Connect with floor supported at HOME.")
        while True:
            try:
                while True:
                    try:
                        message = commands.get_nowait()
                    except queue.Empty:
                        break
                    if message[0] == "quit":
                        return
                    if message[0] == "intent":
                        _, held, enabled, sent_at = message
                        fresh_at = sent_at
                    if message[0] in ("retry", "skip") and connected:
                        if hw:
                            hw.recover()
                        if message[0] == "skip":
                            if operation is None:
                                raise RuntimeError("No selected step to skip; choose a direction first")
                            if hw and operation.action.payload_after:
                                hw.payload(operation.action.payload_after)
                            state = operation.action.destination
                            log.write("OPERATOR_SKIP", state=state, action=operation.action.label,
                                      completion="operator override; not measured")
                            operation = None
                        elif operation:
                            action = operation.action
                            # Retry starts fresh observations/bounds after the
                            # operator has repositioned the hand-held fixture.
                            operation = Operation(action, hw) if hw else type("PreviewOperation", (), dict(action=action, running=False))()
                            log.write("OPERATOR_RETRY", state=state, action=action.label)
                        fault, held, enabled, need_release = False, 0, False, True
                        fresh_at = time.monotonic()
                        report("Recovered. Release, then hold a direction to continue.", "paused")
                    if message[0] == "connect" and not connected and not fault:
                        if live:
                            hw = Hardware(log, report)
                            hw.connect()
                        connected = True
                        if hw and not hw.at_home:
                            state = -2
                        # Preparation can take seconds. Flush pre-connect intent by
                        # requiring a new heartbeat and release after returning.
                        fresh_at = time.monotonic()
                        held, enabled, need_release = 0, False, True
                        report("At HOME. Confirm paths, then hold FORWARD to tighten M1." if state == -1 else
                               "Outside HOME. Only direct GO HOME is available; confirm disengagement/path first.")
                if not connected or fault:
                    if connected and hw:
                        # A software fault stops motion, not RTDE communication.
                        # Do not turn an ordinary bound fault into C207A0.
                        try:
                            hw.maintain_watchdog()
                        except Exception as error:
                            if not getattr(hw, "watchdog_fault_reported", False):
                                log.write("FAULT_WATCHDOG_ERROR", error=str(error))
                                hw.watchdog_fault_reported = True
                    time.sleep(PERIOD_S)
                    continue
                fresh = time.monotonic() - fresh_at < HEARTBEAT_LIMIT_S
                direction = held if enabled and fresh else 0
                if hw:
                    hw.check()
                if operation and operation.running and direction != active_direction:
                    if hw:
                        hw.halt()
                    operation.running = False
                    need_release = True
                    report("Paused. Release and re-press the same direction to resume.", "paused")
                if not fresh and hw:
                    raise RuntimeError("UI heartbeat expired")
                if not direction:
                    need_release = False
                if direction and not need_release:
                    if direction == 2 and (operation is None or active_direction != 2):
                        # Replacing a paused task is deliberate recovery, not a
                        # mechanical undo. Never infer grip state after direct home.
                        action = Action("Direct moveJ HOME — routine reset required", -2, "J", "HOME")
                        operation = Operation(action, hw) if hw else type("PreviewOperation", (), dict(action=action, running=False))()
                        active_direction = 2
                    elif operation and direction != active_direction:
                        if operation.action.kind == "J" and state >= 0 and active_direction != 2:
                            # The original physical endpoint remains `state` until
                            # completion. Reversing retraces the interrupted joint leg.
                            action = ((FORWARD if direction == 1 else REVERSE)[state]
                                      if operation.action.destination == state else
                                      Action("Retrace interrupted joint segment", state, "J", STATE_POINTS[state]))
                            operation = Operation(action, hw) if hw else type("PreviewOperation", (), dict(action=action, running=False))()
                            active_direction = direction
                        else:
                            report("Finish the paused tool/force action before changing direction.", "paused")
                            need_release = True
                    elif operation is None:
                        if state == -1:
                            action = Action("Confirm floor grip: tighten M1", 0, "INITIAL", "HOME", 1) if direction == 1 else None
                        else:
                            action = (FORWARD if direction == 1 else REVERSE).get(state)
                        if action is None:
                            report("End of sequence in this direction. Release the button.", "complete")
                            need_release = True
                        else:
                            active_direction = direction
                            if hw:
                                operation = Operation(action, hw)
                            else:
                                operation = type("PreviewOperation", (), dict(action=action, running=False))()
                            log.write("ACTION_SELECTED", state=state, direction=direction, action=action.label)
                    if operation and not operation.running and not need_release:
                        if hw:
                            operation.start()
                        else:
                            operation.running = True
                            operation.finish_at = time.monotonic() + 1
                        report(operation.action.label + " — release to stop", "running")
                if hw and time.monotonic() - getattr(hw, "last_monitor_sent", 0) >= .2:
                    events.put(dict(monitor=f"UI heartbeat age {(time.monotonic()-fresh_at)*1000:.0f} / max {HEARTBEAT_LIMIT_S*1000:.0f} ms; "
                                    f"feedback age {(time.monotonic()-hw.fresh_at)*1000:.0f} / max 200 ms; watchdog >=3 Hz"))
                    hw.last_monitor_sent = time.monotonic()
                if operation and operation.running:
                    done = operation.tick() if hw else time.monotonic() >= operation.finish_at
                    if hw and operation.debug:
                        if time.monotonic() - getattr(operation, "last_debug_sent", 0) >= .2:
                            events.put(dict(debug=operation.debug))
                            operation.last_debug_sent = time.monotonic()
                    if done:
                        if hw:
                            hw.halt()
                            if operation.action.payload_after:
                                hw.payload(operation.action.payload_after)
                        state = operation.action.destination
                        log.write("ACTION_COMPLETE", state=state, action=operation.action.label)
                        operation, need_release = None, True
                        report("Complete. Release and click again for the next action.", "complete")
                time.sleep(PERIOD_S)
            except Exception as error:
                fault, held, enabled, need_release = True, 0, False, True
                if operation:
                    operation.running = False
                log.write("FAULT", error=str(error), state=state)
                if hw:
                    try:
                        hw.log_fault_snapshot(operation)
                    except Exception as snapshot_error:
                        log.write("FAULT_SNAPSHOT_ERROR", error=str(snapshot_error))
                stop_error = ""
                if hw:
                    try:
                        hw.halt()
                    except Exception as stop:
                        stop_error = "; STOP unconfirmed: " + str(stop)
                if hw and operation and operation.debug:
                    events.put(dict(debug=operation.debug))
                report("Fault: " + str(error) + stop_error + ". Use Retry or Skip after checking the setup.", "fault")

    except Exception as error:
        log.write("FAULT", error=str(error))
        report("FAULT: " + str(error), "fault")
    finally:
        if hw:
            try:
                hw.halt()
            except Exception as error:
                log.write("STOP_UNCONFIRMED", error=str(error))
                report("STOP UNCONFIRMED — use pendant/tool power stop: " + str(error), "fault")
            try:
                hw.close()
            except Exception as error:
                log.write("CLOSE_ERROR", error=str(error))
        log.write("SESSION_END", state=state, live=live)
        log.file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable live Connect; no motion on launch")
    args = parser.parse_args()
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((1080, 930))
    pygame.display.set_caption("E13 — supervised pick and place" + (" LIVE" if args.live else " OFFLINE PREVIEW"))
    font, small = pygame.font.Font(None, 30), pygame.font.Font(None, 24)
    commands, events = mp.Queue(), mp.Queue()
    process = mp.Process(target=worker, args=(commands, events, args.live))
    process.start()
    connect = pygame.Rect(25, 65, 320, 45)
    check = pygame.Rect(25, 210, 28, 28)
    buttons = [(pygame.Rect(25, 275, 490, 90), -1, "HOLD REVERSE"),
               (pygame.Rect(555, 275, 490, 90), 1, "HOLD FORWARD"),
               (pygame.Rect(740, 380, 305, 50), 2, "HOLD moveJ HOME")]
    home_check = pygame.Rect(740, 440, 24, 24)
    home_confirmed = False
    recovery_buttons = [(pygame.Rect(370,65,210,45), "retry", "Retry step"),
                        (pygame.Rect(600,65,210,45), "skip", "Skip step")]
    status = dict(message="Starting offline UI worker...", color="idle", state=-1)
    colors = dict(idle=(60,80,100), running=(155,90,20), paused=(120,100,25), complete=(30,125,65), fault=(150,35,35))
    confirmed, direction, closing = False, 0, False
    clock = pygame.time.Clock()
    def text(message, x, y, width=1020):
        line = ""
        for word in message.split():
            candidate = (line + " " + word).strip()
            if small.size(candidate)[0] > width:
                screen.blit(small.render(line, True, (235,235,235)), (x,y)); y += 25; line = word
            else:
                line = candidate
        screen.blit(small.render(line, True, (235,235,235)), (x,y))
    try:
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or event.type == pygame.KEYDOWN and event.key == pygame.K_q:
                    closing, direction = True, 0
                    commands.put(("quit",))
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    direction = 0
                elif event.type == pygame.MOUSEBUTTONUP:
                    direction = 0
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not closing:
                    if connect.collidepoint(event.pos):
                        if not process.is_alive():
                            process.join()
                            commands, events = mp.Queue(), mp.Queue()
                            process = mp.Process(target=worker, args=(commands, events, args.live))
                            process.start()
                            confirmed, home_confirmed, direction = False, False, 0
                        commands.put(("connect",))
                    elif check.collidepoint(event.pos):
                        confirmed = not confirmed
                    elif home_check.collidepoint(event.pos):
                        home_confirmed = not home_confirmed
                    elif any(rect.collidepoint(event.pos) for rect,_,_ in recovery_buttons):
                        direction = 0
                        for rect, command, _ in recovery_buttons:
                            if rect.collidepoint(event.pos):
                                commands.put((command,))
                    elif confirmed:
                        direction = next((d for rect,d,_ in buttons if rect.collidepoint(event.pos)), 0)
                        if direction == 2 and not home_confirmed:
                            direction = 0
            while True:
                try:
                    status.update(events.get_nowait())
                except queue.Empty:
                    break
            selected = next((rect for rect,d,_ in buttons if d == direction), None)
            if (closing or not confirmed or not pygame.key.get_focused() or not pygame.mouse.get_focused()
                    or not pygame.mouse.get_pressed()[0] or selected is None or not selected.collidepoint(pygame.mouse.get_pos())):
                direction = 0
            # Bound queued heartbeats: worker drains these before acting and
            # checks sender timestamp, not receipt time.
            commands.put(("intent", direction, confirmed and not closing, time.monotonic()))
            screen.fill((20,25,34))
            screen.blit(font.render("E13 — " + ("LIVE HARDWARE" if args.live else "OFFLINE PREVIEW — simulated completion"), True, (245,210,140)), (25,20))
            pygame.draw.rect(screen, (45,85,115), connect)
            text("Connect live" if args.live else "Start offline preview", 40,78)
            for rect,_,label in recovery_buttons:
                pygame.draw.rect(screen, (105,75,35), rect)
                text(label, rect.x+25, rect.y+13, rect.width-30)
            text("Stopping..." if closing else status["message"], 25,130)
            pygame.draw.rect(screen, (45,135,75) if confirmed else (110,110,110), check, 0 if confirmed else 2)
            text("I verified payload/TCP, floor grip/support, paths and stops. HOME is free of external contact for FT zero.", 65,213,980)
            for rect,d,label in buttons:
                pygame.draw.rect(screen, colors[status["color"]], rect, border_radius=7)
                label_surface = font.render(label, True, (255,255,255))
                screen.blit(label_surface, label_surface.get_rect(center=rect.center))
            next_forward = FORWARD.get(status["state"])
            next_reverse = REVERSE.get(status["state"])
            text("Next forward: " + (next_forward.label if next_forward else "Confirm M1 grip" if status["state"] == -1 else "Routine reset required" if status["state"] == -2 else "Cycle finished"),25,390,690)
            text("Next reverse: " + (next_reverse.label if next_reverse else "Unavailable"),25,425,690)
            pygame.draw.rect(screen, (45,135,75) if home_confirmed else (110,110,110), home_check, 0 if home_confirmed else 2)
            text("Tool disengaged; HOME path clear", 775,440,285)
            text("Completed: release and click again. Direct HOME abandons routine; restart to reset attachment state.",25,480)
            text("Force: +/-10 N | XY drift check: DISABLED | Z overtravel: 10 mm | Withdrawal Z tolerance: 0.5 mm | Q: quit",25,510)
            text("Software hold control is not a certified enabling device. Keep pendant and tool power stop available.",25,540)
            text(status.get("monitor", ""), 25, 565)
            for row, line in enumerate(status.get("debug", "").splitlines()):
                text(line, 25, 600 + row*30)
            pygame.display.flip()
            if not process.is_alive():
                if closing:
                    break
                # Preserve a fault on screen until the operator closes it.
                if status["color"] != "fault":
                    status.update(message="Worker exited. Close and restart to reconnect.", color="fault")
            clock.tick(50)
    finally:
        commands.put(("quit",))
        process.join(timeout=5)
        # Do not kill a process that may still be attempting hardware STOP.
        pygame.quit()


if __name__ == "__main__":
    mp.freeze_support()
    main()
