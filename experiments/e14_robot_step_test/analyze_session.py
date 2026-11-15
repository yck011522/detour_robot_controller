"""Offline numerical/session audit; writes CSV, JSON and PNG, never opens RTDE."""

import argparse
import collections
import configparser
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics


def records(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def elapsed(a, b):
    return (datetime.fromisoformat(b["time"]) - datetime.fromisoformat(a["time"])).total_seconds()


def orientation_error(a, b):
    def quat(v):
        theta = math.sqrt(sum(x*x for x in v))
        return [math.cos(theta/2)] + [x*math.sin(theta/2)/theta for x in v] if theta else [1,0,0,0]
    dot = abs(sum(x*y for x,y in zip(quat(a),quat(b))))
    return math.degrees(2*math.acos(min(1., dot)))


def stats(values):
    values = [v for v in values if v is not None]
    return dict(n=len(values), minimum=min(values), median=statistics.median(values), maximum=max(values)) if values else {}


def saved_path(saved, *directories):
    """Prefer the original path, then support a relocated session/archive."""
    original = Path(saved)
    if original.is_file():
        return original
    filename = str(saved).replace("\\", "/").rsplit("/", 1)[-1]
    for directory in directories:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Missing session evidence: {saved}")


def plot_repeatability(force_actions, pauses, session_start, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True, constrained_layout=True)
    series = (
        ("pick", "tighten", 1, "#1976d2", "o"),
        ("place", "tighten", 2, "#e67e22", "s"),
        ("pick", "withdraw", 2, "#239b56", "^"),
        ("place", "withdraw", 1, "#8e44ad", "D"),
    )
    for kind, action, motor, color, marker in series:
        rows = [a for a in force_actions if a["kind"] == kind and a["action"] == action]
        label = f"{kind} {action} M{motor}"
        for ax, metric in zip(axes, ("duration_s", "delta_z_final_mm", "max_xy_mm")):
            ax.plot([a["minutes_from_start"] for a in rows], [a[metric] for a in rows],
                    marker + "-", color=color, label=label)
        for action_row in rows:
            axes[0].annotate(action_row["zone"].upper(),
                             (action_row["minutes_from_start"], action_row["duration_s"]),
                             xytext=(3, 4), textcoords="offset points", fontsize=8)
    for ax in axes:
        ax.grid(alpha=.25)
        for pause in pauses:
            left = elapsed(session_start, {"time": pause["start"]}) / 60
            ax.axvspan(left, left + pause["duration_s"] / 60, color="gray", alpha=.15)
    axes[0].set_ylabel("Action duration (s)")
    axes[1].set_ylabel("Final Z displacement (mm)")
    axes[2].set_ylabel("Maximum lateral displacement (mm)")
    axes[2].set_xlabel("Minutes since session start; shaded areas are pauses")
    axes[0].legend(fontsize=8, ncol=2)
    fig.suptitle("Three-plate session: engagement/release repeatability\n"
                 "Letters identify zones; lines connect observations, not continuous measurements")
    fig.savefig(destination, dpi=160)
    plt.close(fig)


def analyze(directory, make_plot=True):
    directory = Path(directory)
    events = records(directory/"events.jsonl")
    if not events:
        raise ValueError("The session event log is empty")
    poses_config = configparser.ConfigParser()
    poses_config.read(directory/"robot_pose.ini")
    poses = {name: [float(x) for x in poses_config[name]["joint_positions"].split(",")] for name in poses_config.sections()}
    issues, actions, pauses = [], [], []
    pause_start = None
    for event in events:
        m = event["model"]
        if event["event"] == "FAULT":
            issues.append(f"Session fault at {event['time']}: {event.get('reason', '')}")
        plates = [v for v in m["zones"].values() if v] + ([m["held"]] if m["held"] else [])
        if len(plates) != 3 or len(set(plates)) != 3:
            issues.append(f"Inventory inconsistency at {event['time']}")
        if event["event"] == "TRANSFER_SELECTED":
            expected = next((pair for pair in m["queue"] if m["zones"][pair[0]] and not m["zones"][pair[1]]), None)
            if expected != event["transfer"]:
                issues.append(f"Selection differs from first eligible transfer at {event['time']}")
        if event["event"] == "PAUSED":
            pause_start = event
        if event["event"] == "PAUSE_ENDED" and pause_start:
            pauses.append(dict(start=pause_start["time"], duration_s=elapsed(pause_start,event), reason=event["reason"]))
            pause_start = None
        if event["event"] != "OPERATION_STARTED":
            continue
        try:
            transcript = saved_path(event["transcript"], directory)
        except FileNotFoundError as error:
            issues.append(str(error))
            continue
        for line in transcript.read_text(encoding="utf-8").splitlines():
            if not line.startswith("Log: "):
                continue
            try:
                log = saved_path(line[5:].strip(), directory, directory.parent)
                data = records(log)
            except (OSError, ValueError) as error:
                issues.append(str(error))
                continue
            start = next((r for r in data if r["event"] == "ACTION_START"), None)
            complete = next((r for r in data if r["event"] == "CRITERION_REACHED"), None)
            final = next((r for r in data if r["event"] == "FINAL_STATE"), None)
            end = next((r for r in data if r["event"] == "END"), None)
            bad = [r for r in data if r["event"] in ("ACTION_INCOMPLETE", "STOP_UNCONFIRMED", "FINAL_STATE_ERROR", "DISCONNECT_ERROR")]
            issues += [f"{log.name}: {r['event']}: {r.get('reason','')}" for r in bad]
            if not start or not complete or not final or not end or end["outcome"] != "FINISHED":
                issues.append(f"Incomplete action evidence: {log.name}")
                continue
            settings = start["settings"]
            samples = [r for r in data if r["event"] == "SAMPLE"]
            first = next((r for r in data if r["event"] == "STATE"), None)
            if not samples or first is None:
                issues.append(f"Missing state/measurement samples: {log.name}")
                continue
            measurements = samples + [final]
            for r in measurements:
                if r["safety_mode"] != 1 or r["robot_mode"] != 7:
                    issues.append(f"Robot mode change: {log.name}")
            origin = start.get("start_tcp", samples[0]["tcp"])
            current = []
            for r in data:
                if r["event"] == "RX" and r.get("command") == "STATUS" and r["time"] >= start["time"]:
                    words = r["reply"].split()
                    current.append(int(words[3]))
            pose = settings.get("pose")
            row = dict(log=str(log), kind=event["kind"], zone=event.get("zone"), action=settings["action"],
                       motor=settings.get("motor"), pose=pose, time=start["time"],
                       minutes_from_start=elapsed(events[0],start)/60, duration_s=elapsed(start,complete),
                       cleanup_s=elapsed(complete,end), completion=complete.get("reason"),
                       delta_z_at_completion_mm=(samples[-1]["tcp"][2]-start["start_z"])*1000,
                       delta_z_final_mm=(final["tcp"][2]-start["start_z"])*1000,
                       max_xy_mm=max(math.dist(r["tcp"][:2],origin[:2])*1000 for r in measurements),
                       max_orientation_deg=max(orientation_error(r["tcp"][3:],origin[3:]) for r in measurements),
                       max_abs_fx_N=max(abs(r["force"][0]) for r in measurements),
                       max_abs_fy_N=max(abs(r["force"][1]) for r in measurements),
                       max_abs_fz_N=max(abs(r["force"][2]) for r in measurements),
                       initial_fz_N=first["force"][2], final_fz_N=final["force"][2],
                       peak_current_ma=max(current) if current else None,
                       peak_joint_speed_deg_s=max(abs(v)*180/math.pi for r in samples for v in r["joint_velocity"]),
                       arrival_error_deg=max(abs(a-b)*180/math.pi for a,b in zip(final["joints"],poses[pose])) if pose else None,
                       commanded_speed_deg_s=start["movement_settings"]["joint_motion"]["speed_deg_s"])
            actions.append(row)
    for previous, event in zip(events, events[1:]):
        before, after = previous["model"]["queue"], event["model"]["queue"]
        if before != after:
            pair = event.get("transfer")
            if event["event"] != "TRANSFER_COMPLETED" or after != [p for p in before if p != pair] + [pair]:
                issues.append(f"Unexpected queue change at {event['time']}")
    in_pause = False
    for event in events:
        if event["event"] == "PAUSED":
            in_pause = True
        elif event["event"] == "PAUSE_ENDED":
            in_pause = False
        elif in_pause and event["event"] == "STEP_STARTED":
            issues.append(f"Step started while paused at {event['time']}")
    force_actions = [a for a in actions if a["action"] != "move"]
    groups = {}
    for a in force_actions:
        key = f"{a['kind']}_{a['action']}_M{a['motor']}_{a['zone'].upper()}"
        groups.setdefault(key, []).append(a)
    grouped = {key: {metric: stats([a[metric] for a in group]) for metric in
               ("duration_s", "delta_z_at_completion_mm", "delta_z_final_mm", "max_xy_mm", "max_abs_fz_N", "peak_current_ma")}
               for key,group in groups.items()}
    report = dict(start=events[0]["time"], end=events[-1]["time"],
                  wall_minutes=elapsed(events[0], events[-1])/60, pauses=pauses,
                  paused_minutes=sum(p["duration_s"] for p in pauses)/60,
                  actions=len(actions), force_actions=len(force_actions),
                  completed_transfers=events[-1]["model"]["counts"],
                  transfers_this_session=sum(e["event"] == "TRANSFER_COMPLETED" for e in events),
                  final_state=events[-1], issues=issues,
                  completion_reasons=dict(collections.Counter(a["completion"] for a in actions)),
                  action_seconds=sum(a["duration_s"] for a in actions),
                  cleanup_seconds=sum(a["cleanup_s"] for a in actions),
                  arrival_error_deg=stats([a["arrival_error_deg"] for a in actions]),
                  grouped_force_actions=grouped)
    output = directory/"review"
    output.mkdir(exist_ok=True)
    (output/"summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    with (output/"actions.csv").open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream, fieldnames=list(actions[0]) if actions else ["log", "action", "completion"])
        writer.writeheader()
        writer.writerows(actions)
    if make_plot:
        plot_repeatability(force_actions, pauses, events[0], output/"repeatability.png")
    print(json.dumps({k:v for k,v in report.items() if k not in ('final_state','grouped_force_actions')},indent=2))
    print(json.dumps(grouped,indent=2))
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session",type=Path)
    parser.add_argument("--no-plot", action="store_true", help="write CSV/JSON without optional matplotlib")
    args = parser.parse_args()
    analyze(args.session, make_plot=not args.no_plot)
