"""Create a PNG plot and CSV from a step_test JSON-lines log."""

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    records = [json.loads(line) for line in args.log.read_text(encoding="utf-8").splitlines() if line.strip()]
    start = next(r for r in records if r["event"] == "ACTION_START")
    epoch = datetime.fromisoformat(start["time"])
    def seconds(record):
        return (datetime.fromisoformat(record["time"]) - epoch).total_seconds()
    samples = [r for r in records if r["event"] == "SAMPLE"]
    if not samples:
        raise ValueError("No motion samples in log")
    origin = start.get("start_tcp", samples[0]["tcp"])[:3]
    origin[2] = start["start_z"]
    points = samples + [r for r in records if r["event"] == "FINAL_STATE"]
    rows = [[seconds(r)] + [(r["tcp"][i]-origin[i])*1000 for i in range(3)]
            + r["force"] for r in points]
    csv_path = args.log.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["elapsed_s", "dx_mm", "dy_mm", "dz_mm", "fx_N", "fy_N", "fz_N", "tx_Nm", "ty_Nm", "tz_Nm"])
        writer.writerows(rows)
    motors = []
    for r in records:
        if r["event"] == "RX" and r.get("command") == "STATUS" and seconds(r) >= 0:
            words = r["reply"].split()
            motors.append((seconds(r), int(words[3]), int(words[4])))
    fig, axes = plt.subplots(4, 1, figsize=(11, 11), sharex=True, constrained_layout=True)
    colors = ["#1976d2", "#e67e22", "#239b56"]
    for i, color in enumerate(colors):
        axes[0].plot([r[0] for r in rows], [r[1+i] for r in rows], ".-", label="XYZ"[i], color=color)
        axes[1].plot([r[0] for r in rows], [r[4+i] for r in rows], ".-", label="F"+"xyz"[i], color=color)
        axes[2].plot([r[0] for r in rows], [r[7+i] for r in rows], ".-", label="T"+"xyz"[i], color=color)
    axes[0].set_ylabel("TCP displacement (mm)")
    axes[1].set_ylabel("Reported force (N)")
    action = start["settings"]["action"]
    commanded_fz = (0 if action == "loosen-compliant" else
                    -start["settings"]["force"] if action == "tighten" else start["settings"]["force"])
    axes[1].axhline(commanded_fz, color="gray", linestyle=":", label="Commanded Fz (robot on environment)")
    axes[2].set_ylabel("Reported torque (Nm)")
    axes[3].plot([r[0] for r in motors], [r[1] for r in motors], color="#8e44ad", label="Motor current (mA)")
    axes[3].set_ylabel("Motor current (mA)")
    pwm = axes[3].twinx()
    pwm.plot([r[0] for r in motors], [r[2] for r in motors], color="#777777", linestyle="--", label="PWM (%)")
    pwm.set_ylabel("PWM (%)")
    pwm.set_ylim(0, 105)
    for ax in axes:
        ax.axvline(0, color="black", linestyle=":")
        for r in records:
            if r["event"] == "CRITERION_REACHED":
                ax.axvline(seconds(r), color="red", linestyle="--", label="Completion / stop requested")
            elif r["event"] == "ACTION_INCOMPLETE":
                ax.axvline(seconds(r), color="red", linestyle="--", label="Incomplete / stop requested")
        ax.grid(alpha=.25)
        ax.legend(loc="upper left", fontsize=8)
    pwm.legend(loc="upper right", fontsize=8)
    axes[3].set_xlabel("Seconds since action start (host timestamps)")
    fig.suptitle(f"M{start['settings']['motor']} {action} test — {epoch:%Y-%m-%d %H:%M:%S %z}\n"
                 "TCP/force ~5 Hz; tool status sampled more often. Raw reported forces; no sign correction.")
    for suffix in (".png",):
        fig.savefig(args.log.with_suffix(suffix), dpi=160)
    plt.close(fig)
    criterion = next((r for r in records if r["event"] == "CRITERION_REACHED"), None)
    print(json.dumps(dict(samples=len(samples), duration_s=seconds(criterion) if criterion else None,
                          final_delta_mm=rows[-1][1:4],
                          max_logged_current_ma=max(r[1] for r in motors),
                          png=str(args.log.with_suffix('.png')), csv=str(csv_path)), indent=2))


if __name__ == "__main__":
    main()
