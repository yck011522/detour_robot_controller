"""Minimal M1 baseline: inspect the HID and stream stop-only World States.

No UR/tool access and no nonzero driving bits. Run before the application when
checking a configured modem. Close other programs using its COM port first.
"""

import configparser
import json
import os
from pathlib import Path
import struct
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
import pygame
import serial
from serial.tools import list_ports


def main():
    config = configparser.ConfigParser(interpolation=None)
    config.read(Path(__file__).resolve().parents[1] / "config/address.ini")
    port = config["telemetry_modem"]["serial_port"]
    print("Available serial ports:", [p.device for p in list_ports.comports()], flush=True)
    pygame.display.init()
    pygame.joystick.init()
    modem = None
    try:
        if not pygame.joystick.get_count():
            raise RuntimeError("No gamepad at index 0")
        joystick = pygame.joystick.Joystick(0)
        print("Gamepad:", joystick.get_name(), "buttons:", joystick.get_numbuttons(), flush=True)
        if joystick.get_numbuttons() < 12:
            raise RuntimeError("Gamepad needs at least 12 buttons")
        modem = serial.Serial(port, 115200, timeout=0, write_timeout=.02)
        # Keep driver settings fixed after opening; no serial timeout changes.
        buffer = bytearray()
        started = next_tick = time.monotonic()
        sequence, counts, echoes, latest = 1, {}, {}, {}
        while time.monotonic() - started < 5:
            pygame.event.pump()
            modem.write(b"W" + struct.pack("<HB4B", sequence, 0, 1, 1, 1, 1).hex().encode() + b"\n")
            sequence = (sequence + 1) & 65535
            buffer.extend(modem.read(min(modem.in_waiting, 4096)))
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                if len(line.rstrip(b"\r")) != 42:
                    continue
                try:
                    packet = struct.unpack("<BHHB4sHBBBhhH", bytes.fromhex(line.decode()))
                except (ValueError, UnicodeError, struct.error):
                    continue
                car = packet[0]
                if car not in range(1, 5):
                    continue
                counts[car] = counts.get(car, 0) + 1
                echoes.setdefault(car, set()).add(packet[2])
                latest[car] = {"sequence": packet[1], "echo": packet[2], "uid": packet[4].hex(),
                               "controller": packet[7], "follower": packet[8], "battery_mv": packet[11]}
            if len(buffer) > 4096:
                buffer.clear()
            next_tick += .02
            time.sleep(max(0, next_tick - time.monotonic()))
        print(json.dumps({"duration_s": round(time.monotonic() - started, 3),
                          "world_states": sequence - 1, "telemetry_counts": counts,
                          "distinct_echoes": {c: len(v) for c, v in echoes.items()},
                          "latest": latest}, indent=2), flush=True)
    finally:
        if modem:
            try:
                modem.write(b"W" + struct.pack("<HB4B", 300, 0, 1, 1, 1, 1).hex().encode() + b"\n")
            finally:
                modem.close()
        pygame.quit()


if __name__ == "__main__":
    main()
