"""E12: hold a mouse button to traverse taught test poses; release requests stop.

Default is UI preview only. --live enables an explicit Connect button, which
uploads ur_rtde's control script but does not start a trajectory. No power,
brake, tool, protective-stop reset or automatic return-home commands are sent.
"""

import argparse
import configparser
from datetime import datetime
import json
import math
from pathlib import Path
import socket
import threading
import time
import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

# Experiment-local values, as requested. Radians and metres throughout.
TARGET = [4.18499565, -2.79853119, -1.97698689, 0.09257142, 1.53014231, 2.68412971]
HIGH = [4.28639793, -2.15913643, -2.31103086, -0.18984349, 1.48612046, 2.77506161]
HOME = [4.26924419, -1.67344393, -1.85638666, 0.4516658, 1.21078789, 3.12454605]
TAUGHT_TCP = {
    "TARGET": [-0.48796457, -0.48490202, -0.29586709, 3.13030678, -0.10557544, -0.0753362],
    "HIGH": [-0.41571473, -0.47415838, 0.02646051, 3.1196073, -0.08983742, -0.15243114],
    "HOME": [-0.49000971, -0.53404338, 0.70061497, 1.42070816, -0.60378831, -0.63670203],
}
LOW_OFFSET_M = .010  # Base-frame +Z, not tool-local Z.
JOINT_SPEED = math.radians(5)
JOINT_ACCELERATION = math.radians(10)
LINEAR_SPEED = .005
LINEAR_ACCELERATION = .020
STOP_J_DECELERATION = 1.0
STOP_L_DECELERATION = .10
JOINT_TOLERANCE = math.radians(.25)
POSITION_TOLERANCE = .0005
ORIENTATION_TOLERANCE = math.radians(.5)
HEARTBEAT_EXPIRY_S = .15
WATCHDOG_HZ = 5.0
LEG_TIMEOUT_S = 120
NAMES = ("HOME", "HIGH", "LOW", "TARGET")
HERE = Path(__file__).resolve().parent


def orientation_distance(a, b):
    """Quaternion angular distance handles equivalent rotation vectors near pi."""
    def quaternion(vector):
        angle = math.sqrt(sum(v*v for v in vector))
        scale = math.sin(angle / 2) / angle if angle > 1e-12 else .5
        return [math.cos(angle / 2), *(v * scale for v in vector)]
    qa, qb = quaternion(a), quaternion(b)
    dot = abs(sum(x*y for x, y in zip(qa, qb)))
    return 2 * math.acos(min(1, max(0, dot)))


def pose_close(a, b, position=POSITION_TOLERANCE, orientation=ORIENTATION_TOLERANCE):
    return math.dist(a[:3], b[:3]) <= position and orientation_distance(a[3:], b[3:]) <= orientation


def finite_six(values):
    return len(values) == 6 and all(math.isfinite(v) for v in values)


class Route:
    """Remember a partially travelled edge so reversing retraces that same leg."""

    def __init__(self, anchor):
        self.anchor, self.edge = anchor, None

    def leg(self, direction):
        # An unrecognized starting position may only take the explicit HOME
        # approach. After a partial approach, normal route buttons stay blocked.
        if self.anchor is None:
            return (0, "J") if direction == -2 else None
        if direction == -2:
            return None
        if direction not in (-1, 1):
            return None
        if self.edge is None:
            goal = self.anchor + direction
            if goal not in range(4):
                return None
            self.edge = (min(self.anchor, goal), max(self.anchor, goal))
        goal = self.edge[1 if direction > 0 else 0]
        return goal, "L" if self.edge == (2, 3) else "J"

    def arrived(self, goal):
        self.anchor, self.edge = goal, None


class Experiment:
    """One worker owns RTDE control; Pygame only publishes fresh hold intent."""

    def __init__(self, live):
        self.live = live
        self.lock = threading.Lock()
        self.direction, self.heartbeat = 0, 0
        self.allowed = False
        self.close = threading.Event()
        self.thread = None
        self.status = "Preview only; no robot connection" if not live else "Disconnected. Connect first; no automatic movement."
        self.ready, self.details = False, ""
        self.log_file = None

    def log(self, event, **data):
        record = {"time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "event": event, **data}
        print(json.dumps(record), flush=True)
        if self.log_file:
            self.log_file.write(json.dumps(record) + "\n")
            self.log_file.flush()

    def connect(self):
        if not self.live or self.thread is not None:
            return
        self.thread = threading.Thread(target=self.run, name="robot-control", daemon=True)
        self.thread.start()

    def run(self):
        control = receiver = None
        active, stopping, route = None, False, None
        stop_kind = "J"
        completed = False
        def stage(message):
            self.status = message
            self.log("PREPARATION_STEP", step=message)
        try:
            results = HERE / "results"
            results.mkdir(exist_ok=True)
            self.log_file = (results / f"{datetime.now():%Y%m%d_%H%M%S}_hold.log").open("x", encoding="utf-8")
            config = configparser.ConfigParser(interpolation=None)
            config.read(HERE.parents[1] / "config/address.ini")
            host = config["robotic_arm"]["host"]
            stage("Checking Remote Control, stopped program and robot state...")
            # Only read Dashboard state; do not change mode, power or brakes.
            with socket.create_connection((host, 29999), timeout=3) as connection:
                connection.settimeout(3)
                with connection.makefile("rwb", buffering=0) as lines:
                    lines.readline()
                    for command, expected in (("is in remote control", "true"),
                                              ("running", "Program running: false"),
                                              ("robotmode", "Robotmode: RUNNING"),
                                              ("safetystatus", "Safetystatus: NORMAL")):
                        lines.write((command + "\n").encode())
                        actual = lines.readline().decode().strip()
                        if actual != expected:
                            raise RuntimeError(f"{command}: {actual}; expected {expected}")
            from rtde_receive import RTDEReceiveInterface
            from rtde_control import RTDEControlInterface
            stage("Connecting read-only robot feedback...")
            receiver = RTDEReceiveInterface(host, 50.0)
            initial_qd = list(receiver.getActualQd())
            if not finite_six(initial_qd) or max(abs(v) for v in initial_qd) > .01:
                raise RuntimeError("Robot must be stationary before connecting control")
            stage("Uploading / connecting ur_rtde control script (no trajectory)...")
            control = RTDEControlInterface(host)
            self.log("CONTROL_CONNECTED")
            stage("Reading active TCP offset...")
            tcp_offset = list(control.getTCPOffset())
            # ur_rtde 1.6.5 selects a six-double recipe for an all-zero TCP,
            # although FK needs twelve inputs. Force its twelve-double recipe
            # with a 1 picometre calculation-only offset; never change robot TCP.
            fk_tcp = tcp_offset[:]
            if all(value == 0 for value in fk_tcp):
                fk_tcp[0] = 1e-12
            joints = [HOME[:], HIGH[:], None, TARGET[:]]
            poses = [None] * 4
            for index in (0, 1, 3):
                stage(f"Calculating calibrated FK for {NAMES[index]}...")
                poses[index] = list(control.getForwardKinematics(joints[index], fk_tcp))
                self.log("FK_RESULT", point=NAMES[index], pose=poses[index], taught=TAUGHT_TCP[NAMES[index]])
                if not finite_six(poses[index]) or not pose_close(poses[index], TAUGHT_TCP[NAMES[index]], .001, math.radians(1)):
                    raise RuntimeError(f"{NAMES[index]} FK differs from taught TCP: check active TCP/calibration")
                stage(f"Checking controller joint limits for {NAMES[index]}...")
                if not control.isJointsWithinSafetyLimits(joints[index]):
                    raise RuntimeError(f"{NAMES[index]} violates controller limits")
            poses[2] = poses[3][:]
            poses[2][2] += LOW_OFFSET_M
            stage("Checking LOW IK solution near TARGET...")
            if not control.getInverseKinematicsHasSolution(poses[2], TARGET):
                raise RuntimeError("LOW has no IK solution near TARGET")
            stage("Calculating LOW joints...")
            joints[2] = list(control.getInverseKinematics(poses[2], TARGET))
            if not finite_six(joints[2]) or max(abs(a-b) for a, b in zip(joints[2], TARGET)) > math.radians(15):
                raise RuntimeError("LOW IK is invalid or changes branch substantially; inspect it first")
            stage("Checking controller joint limits for LOW...")
            if not control.isJointsWithinSafetyLimits(joints[2]):
                raise RuntimeError("LOW joints violate controller limits")
            for pose in (poses[2], poses[3]):
                stage("Checking controller Cartesian limits for LOW/TARGET...")
                if not control.isPoseWithinSafetyLimits(pose):
                    raise RuntimeError("LOW/TARGET pose violates controller limits")
            stage("Comparing current joints with taught starting points...")
            actual = list(receiver.getActualQ())
            if not finite_six(actual):
                raise RuntimeError("Invalid initial joint feedback")
            anchor = min(range(4), key=lambda i: max(abs(a-b) for a, b in zip(actual, joints[i])))
            joint_error = max(abs(a-b) for a, b in zip(actual, joints[anchor]))
            self.log("START_POSITION", nearest=NAMES[anchor], joints=actual,
                     max_joint_error_deg=math.degrees(joint_error), tolerance_deg=math.degrees(JOINT_TOLERANCE))
            if joint_error > JOINT_TOLERANCE:
                anchor = None
            elif not pose_close(receiver.getActualTCPPose(), poses[anchor], .001, math.radians(1)):
                raise RuntimeError("Current pose does not match the identified taught point")
            route = Route(anchor)
            self.log("PREPARED", tcp_offset=tcp_offset, joints=joints, poses=poses,
                     initial=NAMES[anchor] if anchor is not None else "UNANCHORED", joint_speed_rad_s=JOINT_SPEED, linear_speed_m_s=LINEAR_SPEED)
            self.details = f"TARGET base Z {poses[3][2]*1000:.3f} mm; LOW {poses[2][2]*1000:.3f} mm\nActive TCP offset: {tcp_offset}"
            stage("Enabling communication watchdog (no trajectory)...")
            if not control.setWatchdog(WATCHDOG_HZ):
                raise RuntimeError("Cannot enable robot-side RTDE watchdog")
            self.ready = True
            self.status = (f"At {NAMES[anchor]}. Confirm clearance, then hold a direction." if anchor is not None else
                           "Outside taught points. Verify direct joint path to HOME, then hold GO HOME.")
            # A fresh press is mandatory after setup or after a completed stop.
            seen_hold, needs_release, settled = 0, True, 0
            last_timestamp, fresh_at = receiver.getTimestamp(), time.perf_counter()
            while not self.close.is_set():
                now = time.perf_counter()
                with self.lock:
                    direction = self.direction if self.allowed and now - self.heartbeat < HEARTBEAT_EXPIRY_S else 0
                    ui_fresh = now - self.heartbeat < HEARTBEAT_EXPIRY_S
                if not ui_fresh:
                    raise RuntimeError("UI heartbeat expired; stopping control")
                if receiver.getTimestamp() != last_timestamp:
                    last_timestamp, fresh_at = receiver.getTimestamp(), now
                if now - fresh_at > .2:
                    raise RuntimeError("Robot feedback stopped updating")
                if receiver.getRobotMode() != 7 or receiver.getSafetyMode() != 1 or not control.isProgramRunning():
                    raise RuntimeError("Robot mode/safety/control program changed")
                if not control.kickWatchdog():
                    raise RuntimeError("Watchdog kick failed")
                qd = list(receiver.getActualQd())
                if not finite_six(qd) or not finite_six(receiver.getActualQ()) or not finite_six(receiver.getActualTCPPose()):
                    raise RuntimeError("Invalid robot feedback")
                speed = max(abs(v) for v in qd)
                if active and (not direction or direction != seen_hold):
                    goal, kind, _ = active
                    stop_kind = kind
                    needs_release = True
                    (control.stopL if kind == "L" else control.stopJ)(STOP_L_DECELERATION if kind == "L" else STOP_J_DECELERATION, True)
                    self.log("STOP_REQUESTED", kind=kind, reason="release/focus/direction change")
                    active, stopping, settled, stop_at = None, True, 0, now
                if stopping:
                    settled = settled + 1 if speed < .005 else 0
                    if settled >= 3:
                        stopping = False
                        self.status = "Stopped. Release, then press again to resume or reverse."
                    elif now - stop_at > 3:
                        raise RuntimeError("Stop not confirmed by joint speed")
                elif active:
                    goal, kind, began = active
                    progress = control.getAsyncOperationProgressEx()
                    reached = (pose_close(receiver.getActualTCPPose(), poses[goal]) if kind == "L" else
                               max(abs(a-b) for a, b in zip(receiver.getActualQ(), joints[goal])) <= JOINT_TOLERANCE)
                    if kind == "L" and not progress.isAsyncOperationRunning() and reached:
                        if max(abs(a-b) for a, b in zip(receiver.getActualQ(), joints[goal])) > math.radians(1):
                            raise RuntimeError("Linear endpoint reached on an unexpected joint branch")
                    if not progress.isAsyncOperationRunning() and reached and speed < .005:
                        if seen_hold == -2:
                            needs_release = True
                        route.arrived(goal)
                        active = None
                        self.log("ARRIVED", point=NAMES[goal])
                    elif now - began > LEG_TIMEOUT_S:
                        raise RuntimeError("Leg timeout")
                elif direction and not needs_release:
                    leg = route.leg(direction)
                    if leg:
                        goal, kind = leg
                        self.status = f"Moving {kind}: toward {NAMES[goal]}. Release to stop."
                        active = (goal, kind, now)  # Set before submitting: acceptance can be uncertain on failure.
                        self.log("MOVE_REQUESTED", goal=NAMES[goal], kind=kind)
                        accepted = (control.moveL(poses[goal], LINEAR_SPEED, LINEAR_ACCELERATION, True) if kind == "L" else
                                    control.moveJ(joints[goal], JOINT_SPEED, JOINT_ACCELERATION, True))
                        if not accepted:
                            raise RuntimeError("Motion submission failed; stop requested")
                        seen_hold = direction
                    else:
                        self.status = ("Hold GO HOME first; normal route unavailable." if route.anchor is None else
                                       f"At {NAMES[route.anchor]}; use the normal route buttons.")
                if not direction:
                    needs_release = False
                time.sleep(.02)
            completed = True
        except Exception as error:
            self.status = f"Stopped / fault: {error}. Restart UI after resolving."
            self.log("FAILURE", error=str(error))
        finally:
            self.ready = False
            if control:
                try:
                    if active or stopping:
                        kind = active[1] if active else stop_kind
                        (control.stopL if kind == "L" else control.stopJ)(STOP_L_DECELERATION if kind == "L" else STOP_J_DECELERATION)
                    control.stopScript()
                    self.log("CONTROL_STOPPED")
                except Exception as error:
                    self.log("STOP_UNCONFIRMED", error=str(error))
                    self.status = "STOP UNCONFIRMED: use pendant stop."
                finally:
                    control.disconnect()
            if receiver:
                receiver.disconnect()
            self.log("RESULT", outcome="CLOSED" if completed else "FAULT")
            if self.log_file:
                self.log_file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable explicit live connection; no automatic trajectory")
    args = parser.parse_args()
    experiment = Experiment(args.live)
    pygame.init()
    screen = pygame.display.set_mode((900, 520))
    pygame.display.set_caption("E12 ? hold-to-run robot pose test")
    font, small = pygame.font.Font(None, 30), pygame.font.Font(None, 23)
    connect_rect = pygame.Rect(20, 78, 360, 40)
    check_rect = pygame.Rect(20, 214, 24, 24)
    holds = [(pygame.Rect(65, 266, 350, 75), 1, "HOLD toward TARGET"),
             (pygame.Rect(485, 266, 350, 75), -1, "HOLD toward HOME"),
             (pygame.Rect(225, 350, 450, 55), -2, "HOLD GO HOME (initial approach)")]
    confirmed, closing, running = False, False, True
    clock = pygame.time.Clock()

    def wrapped(message, x, y, width, color=(225, 230, 240)):
        for paragraph in message.splitlines():
            line = ""
            for word in paragraph.split():
                candidate = (line + " " + word).strip()
                if small.size(candidate)[0] > width and line:
                    screen.blit(small.render(line, True, color), (x, y))
                    y += 23
                    line = word
                else:
                    line = candidate
            screen.blit(small.render(line, True, color), (x, y))
            y += 23
        return y

    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_q):
                    closing = True
                    experiment.close.set()
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    with experiment.lock:
                        experiment.direction = 0
                elif event.type == pygame.MOUSEBUTTONUP:
                    with experiment.lock:
                        experiment.direction = 0
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not closing:
                    if connect_rect.collidepoint(event.pos):
                        experiment.connect()
                    elif check_rect.collidepoint(event.pos):
                        confirmed = not confirmed
                    elif experiment.ready and confirmed:
                        for rect, direction, _ in holds:
                            if rect.collidepoint(event.pos):
                                with experiment.lock:
                                    experiment.direction = direction
            with experiment.lock:
                experiment.allowed = confirmed and experiment.ready and not closing
                experiment.heartbeat = time.perf_counter()
                selected = next((rect for rect, direction, _ in holds if direction == experiment.direction), None)
                if (not experiment.allowed or not pygame.key.get_focused() or
                        not pygame.mouse.get_focused() or not pygame.mouse.get_pressed()[0] or
                        selected is None or not selected.collidepoint(pygame.mouse.get_pos())):
                    experiment.direction = 0
                held = experiment.direction
            screen.fill((20, 25, 34))
            screen.blit(font.render("HOME - J - HIGH - J - LOW - L (20 mm) - TARGET", True, (240, 235, 210)), (20, 15))
            wrapped("Return: L TARGET to LOW, then J HIGH to HOME. Release, leave the button, or lose focus to stop.", 20, 48, 860)
            pygame.draw.rect(screen, (50, 85, 110) if args.live else (55, 58, 65), connect_rect)
            screen.blit(small.render("Connect live control (no movement)" if args.live else "PREVIEW - no robot connections", True, (255, 255, 255)), (32, 90))
            wrapped("Stopping control..." if closing else experiment.status, 20, 130, 860, (255, 195, 100))
            pygame.draw.rect(screen, (100, 160, 115) if confirmed else (100, 105, 115), check_rect, 0 if confirmed else 2)
            wrapped("I verified TCP/payload, supported parts and full paths, including direct initial moveJ to HOME.", 56, 216, 815)
            for rect, direction, label in holds:
                color = (140, 75, 30) if held == direction else (45, 80, 110) if experiment.allowed else (50, 55, 65)
                pygame.draw.rect(screen, color, rect, border_radius=6)
                screen.blit(font.render(label, True, (245, 245, 245)), (rect.x + 30, rect.y + 24))
            wrapped(experiment.details, 20, 412, 860)
            wrapped("J: 5 degrees/s | L: 5 mm/s | Escape: stop | Q: close without returning home", 20, 450, 860)
            wrapped("Software hold control is not a certified enabling device. Keep pendant stop available.", 20, 480, 860)
            pygame.display.flip()
            if closing and (experiment.thread is None or not experiment.thread.is_alive()):
                running = False
            clock.tick(50)
    finally:
        with experiment.lock:
            experiment.direction = 0
        experiment.close.set()
        if experiment.thread:
            experiment.thread.join(timeout=3)
        pygame.quit()


if __name__ == "__main__":
    main()
