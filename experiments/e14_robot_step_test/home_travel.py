"""Supervised directed home transfer, stopping at every taught waypoint."""

import argparse
from datetime import datetime
from pathlib import Path

from movement_settings import DEFAULT_CONFIG, load_settings
from zone_flow import HERE, check_start, run_steps, validate_steps


def build_steps(direction):
    prefix, destination = (("bd_ac", "ac_home") if direction == "bd-to-ac" else ("ac_bd", "bd_home"))
    return [(f"Travel to {name}", ["move", "--pose", name, "--payload", "keep", "--profile", "cross_home"])
            for name in [*(f"{prefix}_{i}" for i in range(1, 6)), destination]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("bd-to-ac", "ac-to-bd"))
    parser.add_argument("--payload", required=True, choices=("tool_only", "tool_with_floor"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)
    steps = build_steps(args.direction)
    # Validate the complete named route before opening any connection.
    validate_steps(steps)
    for index, (label, _) in enumerate(steps, 1):
        print(f"{index}. {label}")
    print(f"No blending; payload preserved; configured dwell. Cross-home settings: {settings['cross_home']}")
    if not args.live:
        print("PREVIEW ONLY: no hardware connections.")
        return 0
    if input("Confirm route and held-board clearance; type RUN: ").strip() != "RUN":
        return 1
    check_start("bd_home" if args.direction == "bd-to-ac" else "ac_home", args.payload, settings)
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    transcript = results / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{args.direction}.txt"
    print(f"Transcript: {transcript}", flush=True)
    return run_steps(steps, args.config.resolve(), settings, transcript)


if __name__ == "__main__":
    raise SystemExit(main())
