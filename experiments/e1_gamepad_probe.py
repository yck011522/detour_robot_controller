"""Read 12 joystick buttons at 100 Hz and measure acquisition time."""

import argparse
import math
import os
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def send_to_devices(button_mask):
    """Replace with your transport; bits 0..11 represent buttons B0..B11.

    This placeholder intentionally sends nothing. Avoid blocking I/O here.
    """


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--hz", type=float, default=100)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--list", action="store_true", help="list controllers and exit")
    parser.add_argument("--simulate", action="store_true", help="test loop without hardware; NOT an input benchmark")
    args = parser.parse_args()
    if not math.isfinite(args.hz) or args.hz <= 0 or not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--hz and --seconds must be finite and positive")

    pygame = None
    joystick = None
    try:
        if not args.simulate:
            import pygame

            pygame.display.init()
            pygame.joystick.init()
            # Allow only lifecycle events; button states are polled below.
            pygame.event.set_blocked(None)
            pygame.event.set_allowed([pygame.QUIT, pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED])
            count = pygame.joystick.get_count()
            for index in range(count):
                device = pygame.joystick.Joystick(index)
                print(f"[{index}] {device.get_name()} ({device.get_numbuttons()} buttons)")
            if args.list:
                return
            if not 0 <= args.index < count:
                raise RuntimeError("Selected controller unavailable. Plug it in and run --list.")
            joystick = pygame.joystick.Joystick(args.index)
            if joystick.get_numbuttons() < 12:
                raise RuntimeError("Selected controller exposes fewer than 12 buttons.")
            get_button = joystick.get_button

        print(f"Running at {args.hz:g} Hz for {args.seconds:g}s. Press buttons; Ctrl+C stops.")
        print("SIMULATED INPUT: timings do not measure controller acquisition." if args.simulate else
              "Read timing includes event pump, disconnect checks, 12 reads and mask packing.")
        period = 1 / args.hz
        start = time.perf_counter()
        deadline = start
        previous_tick = None
        read_us, work_us, intervals_ms, lateness_us = [], [], [], []
        skipped = 0
        last_mask = None
        next_report = start + 1
        try:
            while time.perf_counter() - start < args.seconds:
                remaining = deadline - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                tick = time.perf_counter()
                if tick - start >= args.seconds:
                    break
                lateness_us.append(max(0, tick - deadline) * 1e6)
                if previous_tick is not None:
                    intervals_ms.append((tick - previous_tick) * 1e3)
                previous_tick = tick
                acquisition_start = time.perf_counter_ns()
                if args.simulate:
                    mask = 0
                else:
                    # event.get pumps SDL and drains the bounded lifecycle queue.
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            raise KeyboardInterrupt
                        if event.type == pygame.JOYDEVICEREMOVED and event.instance_id == joystick.get_instance_id():
                            raise RuntimeError("Controller disconnected; stopped sending. Reconnect and restart.")
                    mask = 0
                    for button in range(12):
                        mask |= int(get_button(button)) << button
                read_us.append((time.perf_counter_ns() - acquisition_start) / 1000)
                send_to_devices(mask)
                work_us.append((time.perf_counter() - tick) * 1e6)
                # Console output stays outside the acquisition measurement.
                now = time.perf_counter()
                if now >= next_report:
                    print(f"B0..B11={''.join(str((mask >> b) & 1) for b in range(12))}  "
                          f"mask=0x{mask:03x}  latest read={read_us[-1]:.1f} us")
                    next_report = now + 1
                last_mask = mask
                deadline += period
                now = time.perf_counter()
                if now > deadline:
                    missed = math.floor((now - deadline) / period) + 1
                    skipped += missed
                    deadline += missed * period
        except KeyboardInterrupt:
            pass
        finally:
            if read_us:
                print(f"\nSamples: {len(read_us)}; skipped scheduled ticks: {skipped}")
                for label, values, unit in [("Input acquisition", read_us, "us"),
                                             ("Acquisition + send hook", work_us, "us"),
                                             ("Wake lateness", lateness_us, "us"),
                                             ("Tick interval", intervals_ms, "ms")]:
                    if values:
                        print(f"{label}: median={percentile(values, .5):.2f}, "
                              f"p95={percentile(values, .95):.2f}, p99={percentile(values, .99):.2f}, "
                              f"max={max(values):.2f} {unit}")
                print(f"Reads >=500 us: {sum(value >= 500 for value in read_us)}/{len(read_us)}")
                print(f"Last button mask: 0x{last_mask:03x}" if last_mask is not None else "No completed sends")
    finally:
        if pygame is not None:
            pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ImportError) as error:
        raise SystemExit(str(error))
