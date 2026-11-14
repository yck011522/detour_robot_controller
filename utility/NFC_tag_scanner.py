"""Read the wired USB NFC scanner and save named UIDs to an INI file."""

import argparse
import configparser
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

import serial
from serial.tools import list_ports


TAG_FILE = Path(__file__).resolve().parents[1] / "config" / "dynamic_nfc.ini"
BAUDRATE = 115200


def normalize_uid(value):
    value = re.sub(r"[\s:-]", "", value).upper()
    return value if re.fullmatch(r"[0-9A-F]{8}", value) else ""


def parse_packet(raw):
    """Match the NFC/AGE_MS/READER format used by the existing Rhino mapper."""
    try:
        fields = {}
        for part in raw.decode("ascii").strip().split(","):
            key, separator, value = part.partition("=")
            if separator:
                fields[key.strip().upper()] = value.strip()
        if not all(key in fields for key in ("NFC", "AGE_MS", "READER")):
            return None
        uid = normalize_uid(fields["NFC"])
        if fields["NFC"].upper() != "NONE" and not uid:
            return None
        age = int(fields["AGE_MS"]) if fields["AGE_MS"].isdecimal() else None
        return uid, age, fields["READER"]
    except (UnicodeDecodeError, ValueError):
        return None


@dataclass(frozen=True)
class Scan:
    uid: str = ""
    age_ms: int | None = None
    received: float = 0
    reader: str = "Waiting"
    error: str = ""

    def age(self, now):
        if not self.uid or self.age_ms is None:
            return None
        return self.age_ms + int(max(0, now - self.received) * 1000)


class Scanner:
    """Read continuously, including while the console is prompting for a name."""

    def __init__(self, connection):
        self.connection = connection
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = Scan()
        self.thread = threading.Thread(target=self.run, name="USB-NFC", daemon=True)

    def snapshot(self):
        with self.lock:
            return self.latest

    def run(self):
        buffer = bytearray()
        discarding = False
        try:
            while not self.stop.is_set():
                raw = self.connection.readline(4096)
                if not raw:
                    continue
                if discarding:
                    discarding = not raw.endswith(b"\n")
                    continue
                buffer.extend(raw)
                if len(buffer) > 4096:
                    buffer.clear()
                    discarding = not raw.endswith(b"\n")
                    continue
                if not buffer.endswith(b"\n"):
                    continue  # Serial timeouts can split a message across reads.
                packet = parse_packet(bytes(buffer))
                buffer.clear()
                if packet is None:
                    continue
                uid, age, reader = packet
                with self.lock:
                    if uid:
                        self.latest = Scan(uid, age, time.monotonic(), reader)
                    else:
                        # No-read must not erase the last scanned UID or reset age.
                        self.latest = replace(self.latest, reader=reader)
        except Exception as error:
            with self.lock:
                self.latest = replace(self.latest, error=str(error))

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        self.connection.close()


def load_tags(path):
    tags = configparser.ConfigParser(interpolation=None)
    if path.exists():
        with path.open(encoding="utf-8-sig") as source:
            tags.read_file(source)
    if not tags.has_section("tags"):
        tags.add_section("tags")
    seen = {}
    for name, value in tags["tags"].items():
        uid = normalize_uid(value)
        if not uid:
            raise ValueError(f"Invalid saved UID for {name}: {value!r}")
        if uid in seen:
            raise ValueError(f"UID {uid} is assigned to both {seen[uid]} and {name}")
        seen[uid] = name
    return tags


def save_tag(path, name, uid, *, overwrite=False):
    name = name.strip().lower()
    uid = normalize_uid(uid)
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Use a name starting with a letter, then letters, digits, _ or -.")
    if not uid:
        raise ValueError("Expected a four-byte UID (eight hexadecimal digits).")
    tags = load_tags(path)
    for other, value in tags["tags"].items():
        if other != name and normalize_uid(value) == uid:
            raise ValueError(f"UID {uid} is already assigned to {other}.")
    previous = tags["tags"].get(name)
    if previous and normalize_uid(previous) != uid and not overwrite:
        raise ValueError(f"Name {name} already has UID {previous}.")
    tags["tags"][name] = uid
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            target.write("# Named physical NFC tags captured by utility/NFC_tag_scanner.py.\n")
            tags.write(target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_key():
    if os.name == "nt":
        import msvcrt
        if not msvcrt.kbhit():
            return ""
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            msvcrt.getwch()  # Consume the second byte of a special key.
            return ""
        if key == "\x03":
            raise KeyboardInterrupt
        return key.lower()
    import select
    return sys.stdin.readline().strip().lower() if select.select([sys.stdin], [], [], 0)[0] else ""


def choose_port():
    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    if not ports:
        raise RuntimeError("No serial ports found. Connect the USB NFC scanner.")
    for index, port in enumerate(ports, 1):
        print(f"{index}: {port.device} | {port.description} | {port.hwid}")
    answer = input("Scanner port (number or COM name; Q cancels): ").strip()
    if answer.lower() == "q":
        return None
    if answer.isdecimal() and 1 <= int(answer) <= len(ports):
        return ports[int(answer) - 1].device
    match = next((port.device for port in ports if port.device.lower() == answer.lower()), None)
    if match:
        return match
    raise ValueError("Choose one of the listed ports, or use --port COMx.")


def scan_tags(scanner, path):
    print(f"Saving to {path}")
    print("S: capture latest tag and enter its name | Q: quit | Ctrl+C: quit")
    if os.name != "nt":
        print("Press Enter after commands.")
    while True:
        current = scanner.snapshot()
        if current.error:
            raise RuntimeError(f"Scanner disconnected: {current.error}")
        age = current.age(time.monotonic())
        text = f"Last tag: {current.uid or '--'} | Age: {str(age) + ' ms' if age is not None else '--'} | Reader: {current.reader}"
        print("\r" + text.ljust(100), end="", flush=True)
        key = read_key()
        if key == "q":
            print()
            return
        if key == "s":
            captured = scanner.snapshot()  # Freeze UID before the naming prompt.
            print()
            if not captured.uid:
                print("No valid tag has been scanned yet.")
                continue
            if captured.error:
                raise RuntimeError(f"Scanner disconnected: {captured.error}")
            print(f"Captured: {captured.uid}")
            name = input("Tag name, e.g. p1 (blank cancels): ").strip().lower()
            if not name:
                continue
            tags = load_tags(path)
            previous = tags["tags"].get(name)
            overwrite = False
            if previous and normalize_uid(previous) != captured.uid:
                overwrite = input(f"Replace {name} = {previous} with {captured.uid}? [y/N]: ").strip().lower() == "y"
                if not overwrite:
                    continue
            try:
                save_tag(path, name, captured.uid, overwrite=overwrite)
                print(f"Saved {name} = {captured.uid}")
            except ValueError as error:
                print(f"Not saved: {error}")
        time.sleep(.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Scanner serial port, e.g. COM12; otherwise choose from a list")
    parser.add_argument("--output", type=Path, default=TAG_FILE, help="INI path (default: config/dynamic_nfc.ini)")
    args = parser.parse_args()
    scanner = None
    try:
        load_tags(args.output)  # Catch malformed saved data before opening hardware.
        port = args.port or choose_port()
        if not port:
            return 0
        scanner = Scanner(serial.Serial(port, baudrate=BAUDRATE, timeout=.2))
        scanner.thread.start()
        print(f"Connected to {port} at {BAUDRATE} baud.")
        scan_tags(scanner, args.output)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nScanner closed.")
        return 0
    except (OSError, ValueError, RuntimeError, configparser.Error, serial.SerialException) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
    finally:
        if scanner is not None:
            scanner.close()


if __name__ == "__main__":
    raise SystemExit(main())
