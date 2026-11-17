"""One supervised robot action per invocation: a joint move, tightening or release.

The robot process runs each step of an operation as a separate invocation of
this module, so every action opens and cleans up its own connections.
"""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import time

from . import tool as tool_protocol
from .settings import MOVEMENT_CONFIG, ROOT, force_completion, load_payload, load_poses, load_settings

LOG_DIRECTORY = ROOT / "logs/robot"


class Log:
    def __init__(self):
        LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIRECTORY / f"{datetime.now():%Y%m%d_%H%M%S_%f}.log"
        self.file = self.path.open("x", encoding="utf-8")
        print(f"Log: {self.path}")

    def write(self, event, **data):
        line = json.dumps(dict(time=datetime.now().astimezone().isoformat(), event=event, **data))
        self.file.write(line + "\n")
        self.file.flush()
        print(line, flush=True)


def settled_matches(joints, poses):
    return {name: math.degrees(max(abs(a - b) for a, b in zip(joints, q)))
            for name, q in poses.items()}


def snapshot(receiver):
    result = dict(joints=list(receiver.getActualQ()),
                  joint_velocity=list(receiver.getActualQd()),
                  tcp=list(receiver.getActualTCPPose()),
                  force=list(receiver.getActualTCPForce()),
                  timestamp=receiver.getTimestamp(),
                  safety_mode=receiver.getSafetyMode(), robot_mode=receiver.getRobotMode())
    for key in ("joints", "joint_velocity", "tcp", "force"):
        if len(result[key]) != 6 or not all(math.isfinite(v) for v in result[key]):
            raise RuntimeError(f"Invalid {key} feedback")
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("move", "tighten", "withdraw"))
    result.add_argument("--live", action="store_true", help="required: this commands the real robot")
    result.add_argument("--config", type=Path, default=MOVEMENT_CONFIG)
    result.add_argument("--pose", help="named destination for a single direct MoveJ")
    result.add_argument("--motor", type=int, choices=(1, 2))
    result.add_argument("--payload", choices=("tool_only", "tool_with_floor", "keep"), default="keep",
                        help="payload to apply before the action")
    result.add_argument("--profile", choices=("local", "cross_home"), default="local",
                        help="MoveJ speed/timeout profile; force actions always use local settings")
    return result


def run(args):
    movement = load_settings(args.config)
    joint = movement["joint_motion"]
    runtime = movement["runtime"]
    if not args.live:
        raise ValueError("Robot actions require --live")
    if args.profile == "cross_home":
        if args.action != "move":
            raise ValueError("Cross-home profile is only valid for MoveJ")
        joint["speed_deg_s"] = movement["cross_home"]["speed_deg_s"]
        joint["timeout_seconds"] = movement["cross_home"]["timeout_seconds"]
    force_action = args.action != "move"
    if force_action and args.motor is None:
        raise ValueError("Tighten/withdraw requires --motor 1 or 2")
    if force_action and args.payload != "keep":
        raise ValueError("Engaged motor actions require --payload keep; set payload separately when clear")
    force = (movement["tightening"]["downward_force_n"] if args.action == "tighten" else
             movement["placement_release" if args.motor == 1 else "pickup_release"]["upward_force_n"]
             if args.action == "withdraw" else 0.)
    seconds = movement["tightening" if args.action == "tighten" else
                       "release" if force_action else "joint_motion"]["timeout_seconds"]
    poses = load_poses()
    if args.action == "move" and args.pose not in poses:
        raise ValueError("Move requires --pose naming an existing taught pose")
    log = Log()
    receiver = control = tool = None
    force_attempted = False
    failed = False
    try:
        from rtde_receive import RTDEReceiveInterface
        receiver = RTDEReceiveInterface(tool_protocol.host_from_config(), runtime["receive_hz"])
        time.sleep(joint["settle_seconds"])
        initial = snapshot(receiver)
        errors = settled_matches(initial["joints"], poses)
        log.write("STATE", **initial, joint_errors_deg=errors,
                  arrival_tolerance_deg=joint["arrival_tolerance_deg"],
                  matching_poses=[n for n, e in errors.items() if e <= joint["arrival_tolerance_deg"]])
        print("ONE ACTION ONLY:", vars(args))
        print("Move is a DIRECT joint leg, not an automatic route. M1: floor/tool connection. M2: floor/structure connection.")
        if input("Confirm support, disengagement/path and payload; type RUN: ").strip() != "RUN":
            log.write("CANCELLED")
            failed = True
            return 1
        initial = snapshot(receiver)
        if initial["safety_mode"] != 1 or initial["robot_mode"] != 7:
            raise RuntimeError("Restore robot NORMAL safety / RUNNING first")
        if max(map(abs, initial["joint_velocity"])) > joint["stationary_velocity_rad_s"]:
            raise RuntimeError("Robot must be stationary before the test")
        from rtde_control import RTDEControlInterface
        control = RTDEControlInterface(tool_protocol.host_from_config())
        tool = tool_protocol.LineClient(tool_protocol.host_from_config(), tool_protocol.tool_port(), log,
                                        timeout=runtime["tool_reply_timeout_seconds"])
        tool_protocol.stop_tool(tool)
        if args.payload == "keep":
            log.write("PAYLOAD_PRESERVED", mass_kg=receiver.getPayload(),
                      cog=list(receiver.getPayloadCog()), note="No setPayload or FT zero command")
        else:
            mass, cog = load_payload(args.payload)
            if not control.setPayload(mass, cog):
                raise RuntimeError("Payload command rejected")
        if not control.setWatchdog(runtime["watchdog_min_hz"]):
            raise RuntimeError("Watchdog setup failed")
        initial = snapshot(receiver)
        start_z = initial["tcp"][2]
        started = time.monotonic()
        last_stamp, fresh_at = initial["timestamp"], started
        settled_at = None

        def apply_force():
            nonlocal force_attempted
            force_attempted = True
            fz = -force if args.action == "tighten" else force
            if not control.forceMode([0.] * 6, [1, 1, 1, 0, 0, 0], [0, 0, fz, 0, 0, 0], 2,
                                     [movement["compliance"]["translation_speed_mm_s"] / 1000] * 3 +
                                     [math.radians(movement["compliance"]["orientation_deviation_deg"])] * 3):
                raise RuntimeError("Force mode rejected")

        if args.action == "move":
            if not control.moveJ(poses[args.pose], math.radians(joint["speed_deg_s"]),
                                 math.radians(joint["acceleration_deg_s2"]), True):
                raise RuntimeError("MoveJ rejected")
        else:
            # Engagement must never begin against a position-holding arm.
            apply_force()
            log.write("FORCE_MODE_STARTED", commanded_fz_N=-force if args.action == "tighten" else force)
            command = f"{'TIGHTEN' if args.action == 'tighten' else 'LOOSEN'} M{args.motor}"
            tool.command(command, "OK " + command)
            started = time.monotonic()
        log.write("ACTION_START", settings={**vars(args), "config": str(args.config)},
                  movement_settings=movement, start_z=start_z, start_tcp=initial["tcp"])
        last_report = 0.
        while True:
            if not control.kickWatchdog():
                raise RuntimeError("Watchdog update failed")
            data = snapshot(receiver)
            now = time.monotonic()
            if data["timestamp"] != last_stamp:
                last_stamp, fresh_at = data["timestamp"], now
            if (now - fresh_at > runtime["feedback_timeout_seconds"] or data["safety_mode"] != 1
                    or data["robot_mode"] != 7 or not control.isProgramRunning()):
                raise RuntimeError("Feedback stale or robot safety/mode/program changed")
            if now - started > seconds and args.action == "move":
                raise RuntimeError("Move window ended; action incomplete")
            done = False
            completion_reason = None
            motor = None
            if args.action == "move":
                running = control.getAsyncOperationProgressEx().isAsyncOperationRunning()
                stationary = max(map(abs, data["joint_velocity"])) < joint["stationary_velocity_rad_s"]
                if not running and stationary:
                    if settled_at is None:
                        settled_at = now
                    if now - settled_at >= joint["settle_seconds"]:
                        error = settled_matches(data["joints"], {args.pose: poses[args.pose]})[args.pose]
                        if error > joint["arrival_tolerance_deg"]:
                            raise RuntimeError(f"Move ended {error:.3f} degrees from destination")
                        done = True
                        completion_reason = "joint_arrival"
                else:
                    settled_at = None
            else:
                dz = data["tcp"][2] - start_z
                # Check tightening overtravel before a potentially slow tool query.
                if args.action == "tighten":
                    force_completion(args.action, dz * 1000, now - started, "TIGHTENING", movement, seconds)
                motor = tool_protocol.status(tool)
                own = motor["states"][args.motor - 1]
                if motor["states"][2 - args.motor] != "IDLE":
                    raise RuntimeError("Other motor unexpectedly active")
                expected = "TIGHTENING" if args.action == "tighten" else "LOOSENING"
                if own not in (expected, "STALLED"):
                    raise RuntimeError(f"Unexpected motor state: {own}")
                completion_reason = force_completion(args.action, dz * 1000, now - started, own, movement, seconds)
                done = completion_reason is not None
                if not done:
                    apply_force()
            if now - last_report >= runtime["sample_interval_seconds"] or done:
                log.write("SAMPLE", **data, delta_z_mm=(data["tcp"][2] - start_z) * 1000, motor=motor)
                last_report = now
            if done:
                log.write("CRITERION_REACHED", action=args.action, reason=completion_reason)
                break
            time.sleep(runtime["loop_sleep_seconds"])
    except (Exception, KeyboardInterrupt) as error:
        failed = True
        log.write("ACTION_INCOMPLETE", reason=str(error) or "Operator Ctrl+C")
    finally:
        # Attempt all cleanup operations even when an earlier stop fails.
        cleanup = []
        if tool:
            cleanup.append(("tool_stop", lambda: tool_protocol.stop_tool(tool)))
        if control:
            cleanup.append(("motion_stop", lambda: control.stopJ(runtime["stop_joint_deceleration_rad_s2"])
                            if args.action == "move" else control.stopL(runtime["stop_linear_deceleration_m_s2"])))
            if force_attempted:
                cleanup.append(("force_stop", control.forceModeStop))
            cleanup.append(("script_stop", control.stopScript))
        for name, operation in cleanup:
            try:
                if operation() is False:
                    raise RuntimeError("Command returned false")
                log.write("CLEANUP_COMPLETE", step=name)
            except Exception as error:
                failed = True
                log.write("STOP_UNCONFIRMED", step=name, reason=str(error))
        if receiver:
            try:
                time.sleep(joint["settle_seconds"])
                final = snapshot(receiver)
                log.write("FINAL_STATE", **final)
                if max(map(abs, final["joint_velocity"])) > joint["stationary_velocity_rad_s"]:
                    failed = True
                    log.write("STOP_UNCONFIRMED", reason="Joint velocity remains nonzero")
            except Exception as error:
                failed = True
                log.write("FINAL_STATE_ERROR", reason=str(error))
        for connection in (tool, control, receiver):
            if connection:
                try:
                    (connection.close if connection is tool else connection.disconnect)()
                except Exception as error:
                    failed = True
                    log.write("DISCONNECT_ERROR", reason=str(error))
        log.write("END", outcome="INCOMPLETE" if failed else "FINISHED")
        log.file.close()
    return int(failed)


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except (ValueError, KeyError) as error:
        raise SystemExit(str(error))
