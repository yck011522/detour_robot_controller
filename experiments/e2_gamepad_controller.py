"""E2: read 12 gamepad buttons at 100 Hz; print every tenth read."""

import argparse
import os
import time

# Configure SDL before importing pygame, including input while unfocused.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

import pygame

BUTTON_COUNT = 12
PERIOD = 0.01  # 100 Hz means one read every 10 milliseconds.


def read_buttons(joystick):
    """Return the current buttons in B0-to-B11 order after the loop pumps SDL."""
    # The loop handles lifecycle events before calling this state-only reader.
    return tuple(int(joystick.get_button(button)) for button in range(BUTTON_COUNT))


def try_connect(index):
    """Try opening the selected device; return None if it is not ready."""
    # Enumeration indexes can change after unplugging; each retry looks up the
    # current index afresh instead of reusing a disconnected joystick object.
    joystick = None
    try:
        if index >= pygame.joystick.get_count():
            return None
        joystick = pygame.joystick.Joystick(index)
        if joystick.get_numbuttons() >= BUTTON_COUNT:
            return joystick
    except pygame.error:
        # Unplugging during enumeration/opening is recoverable on the next tick.
        pass
    if joystick is not None:
        disconnect(joystick)
    return None


def disconnect(joystick):
    """Release an old handle even if its underlying device has disappeared."""
    # A device removal can make SDL cleanup fail; keep the game loop alive.
    try:
        joystick.quit()
    except pygame.error:
        pass


def run(index):
    """Keep the loop alive, reconnect as needed, and print every tenth sample."""
    joystick = None
    connected = False
    instance_id = None
    # The clock is used only to schedule reads, not to measure performance.
    next_tick = time.perf_counter()
    reads = 0
    while True:
        delay = next_tick - time.perf_counter()
        if delay > 0:
            time.sleep(delay)

        # Pump SDL even while disconnected so hot-plug updates are processed.
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise KeyboardInterrupt
            if (
                event.type == pygame.JOYDEVICEREMOVED
                and connected
                and event.instance_id == instance_id
            ):
                disconnect(joystick)
                joystick = None
                connected = False
                print("Controller disconnected; waiting to reconnect.")

        # Retry on each disconnected tick, including when no device was plugged
        # in at startup. Print only on connection changes, not on every retry.
        if not connected:
            joystick = try_connect(index)
            if joystick is not None:
                try:
                    instance_id = joystick.get_instance_id()
                    name = joystick.get_name()
                    connected = True
                    print(f"Controller connected: {name}")
                except pygame.error:
                    disconnect(joystick)
                    joystick = None

        # Released buttons are the default while offline; never reuse stale
        # pressed states. Handle unplugging during the read as well as events.
        buttons = (0,) * BUTTON_COUNT
        if connected:
            try:
                buttons = read_buttons(joystick)
            except pygame.error:
                disconnect(joystick)
                joystick = None
                connected = False
                print("Controller unavailable; waiting to reconnect.")
        reads += 1
        if reads == 10:
            if connected:
                # B0 is the leftmost digit; 1 means pressed and 0 means released.
                print("B0..B11=" + "".join(str(button) for button in buttons))
            else:
                print("Controller unavailable; waiting to reconnect.")
            reads = 0

        # Advance the schedule without adding read/print time to every period.
        # If work overruns, wait a fresh period instead of doing catch-up reads.
        next_tick += PERIOD
        now = time.perf_counter()
        if next_tick < now:
            next_tick = now + PERIOD


def main():
    """Open the selected controller and release pygame resources when finished."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index", type=int, default=0, help="controller index (default: 0)"
    )
    args = parser.parse_args()
    if args.index < 0:
        parser.error("--index must be nonnegative")

    try:
        pygame.display.init()
        pygame.joystick.init()
        # Only lifecycle events are needed because we poll buttons directly.
        pygame.event.set_blocked(None)
        pygame.event.set_allowed(
            [pygame.QUIT, pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED]
        )
        print("Reading at 100 Hz; printing every 10 reads. Ctrl+C stops.")
        print("Waiting for a controller with at least 12 buttons.")
        run(args.index)
    finally:
        # Cleanup happens when the user stops the loop, including while offline.
        pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
    except RuntimeError as error:
        raise SystemExit(str(error))
