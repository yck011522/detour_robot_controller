"""The existing E15 wire contract. No map, robot or device-library dependencies."""

from dataclasses import dataclass
import re
import struct

WORLD = struct.Struct("<HB4B")
ROBOT = struct.Struct("<BHHB4sHBBBhhH")
HEX_ROBOT = re.compile(rb"[0-9a-fA-F]{42}\Z")
AUTO_STOP = 0x01
CONTROLLERS = {0: "AUTO", 1: "MANUAL", 11: "NFC periodic recovery",
               12: "NFC bus recovery", 13: "NFC tag-order recovery",
               14: "LOW BATTERY", 15: "STOPPED"}
FOLLOWERS = {0: "CENTER", 1: "TURN LEFT", 2: "TURN RIGHT", 3: "APPROACH",
             4: "BRANCH CENTER", 5: "LEFT EDGE", 6: "RIGHT EDGE",
             7: "MERGE CENTER", 8: "MERGE LEFT", 9: "MERGE RIGHT", 10: "LINE LOST"}


def encode_world(sequence, topology, movements):
    """Return exactly W + fourteen hex digits + LF; never send raw BLE bytes."""
    if len(movements) != 4 or any(not 0 <= value <= 31 for value in movements):
        raise ValueError("Four movement bytes using bits 0-4 are required")
    return b"W" + WORLD.pack(sequence, topology, *movements).hex().encode("ascii") + b"\n"


@dataclass(frozen=True)
class Telemetry:
    car: int
    sequence: int
    echo: int
    lost: int
    uid: bytes
    nfc_age_ms: int
    sensors: int
    controller: int
    follower: int
    left_mm_s: int
    right_mm_s: int
    battery_mv: int

    @classmethod
    def decode(cls, line):
        line = line.rstrip(b"\r")
        if not HEX_ROBOT.fullmatch(line):
            raise ValueError("Expected exactly 42 hex characters")
        packet = cls(*ROBOT.unpack(bytes.fromhex(line.decode("ascii"))))
        if packet.car not in range(1, 5):
            raise ValueError("Invalid logical car ID")
        return packet


def movement(forward=False, left=False, right=False, *, manual=False, backward=False):
    """Encode movement bits after the PC's tag-aware AUTO steering filter.

    Both physical turns are treated as neutral, rather than relying on firmware
    tie-breaking. No-turn AUTO remains center-following; MANUAL can pivot.
    """
    if left and right:
        left = right = False
    if manual and forward and backward:
        return 0  # Stopped MANUAL, with no pivot left behind.
    return (0 if manual else 1) | (2 if forward else 0) | (4 if manual and backward else 0) | (8 if left else 0) | (16 if right else 0)
