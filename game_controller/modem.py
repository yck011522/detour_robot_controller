"""One serial owner, bounded ordered telemetry and latest-only command delivery."""

from collections import deque
import threading
import time

import serial
from serial.tools import list_ports

from .protocol import Telemetry, encode_world


def open_modem(port):
    """Initial open is fatal; never silently switch to another COM port."""
    try:
        return serial.Serial(port, 115200, timeout=0, write_timeout=.02)
    except serial.SerialException as error:
        available = ", ".join(p.device for p in list_ports.comports()) or "none"
        raise RuntimeError(f"Cannot open configured modem {port}: {error}. Available: {available}") from error


class Modem:
    """No driver reconfiguration after open; serial waits never clock HID/rendering."""

    def __init__(self, port, stream, command_expiry, reconnect_seconds, log):
        self.port, self.stream, self.log = port, stream, log
        self.command_expiry, self.reconnect_seconds = command_expiry, reconnect_seconds
        self.lock = threading.Lock()
        self.received = deque()
        self.sent = deque()
        self.latest = None
        self.revision = 0
        self.connected, self.session = True, 1
        self.error, self.overflow, self.invalid = "", False, 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="modem", daemon=True)
        self.thread.start()

    def submit(self, sequence, topology, commands):
        with self.lock:
            self.revision += 1
            self.latest = (self.revision, time.perf_counter(), sequence, topology, tuple(commands))

    def drain(self):
        with self.lock:
            received, sent = list(self.received), list(self.sent)
            self.received.clear()
            self.sent.clear()
            state = {"connected": self.connected, "session": self.session, "error": self.error,
                     "overflow": self.overflow, "invalid_lines": self.invalid}
        return received, sent, state

    def run(self):
        buffer, discard = bytearray(), False
        sent_revision, retry_at = -1, 0
        try:
            while not self.stop.is_set():
                if self.stream is None:
                    if time.perf_counter() < retry_at:
                        self.stop.wait(.01)
                        continue
                    try:
                        self.stream = open_modem(self.port)
                        with self.lock:
                            self.connected = True
                            self.error = ""
                            self.session += 1
                        sent_revision = -1
                        buffer.clear()
                        discard = False
                        self.log("MODEM_RECONNECTED", port=self.port, session=self.session)
                    except RuntimeError as error:
                        with self.lock:
                            self.error = str(error)
                        retry_at = time.perf_counter() + self.reconnect_seconds
                        continue
                try:
                    data = self.stream.read(min(self.stream.in_waiting, 16384))
                    for byte in data:
                        if byte == 10:
                            if not discard:
                                raw = bytes(buffer)
                                try:
                                    packet = Telemetry.decode(raw)
                                    received_at = time.perf_counter()
                                    with self.lock:
                                        if len(self.received) >= 1024:
                                            self.overflow = True
                                        else:
                                            self.received.append((received_at, self.session, packet))
                                    self.log("RX", port=self.port, raw=raw.decode("ascii"), session=self.session)
                                except ValueError:
                                    with self.lock:
                                        self.invalid += 1
                                    self.log("RX_REJECTED", raw_hex=raw.hex())
                            buffer.clear()
                            discard = False
                        elif not discard:
                            if len(buffer) >= 1024:
                                buffer.clear()
                                discard = True
                                with self.lock:
                                    self.invalid += 1
                            else:
                                buffer.append(byte)
                    with self.lock:
                        latest = self.latest
                    if latest and latest[0] != sent_revision:
                        revision, generated, sequence, topology, commands = latest
                        # An I/O thread must not keep the car watchdog alive with
                        # old driving intent when the central controller stalls.
                        if time.perf_counter() - generated <= self.command_expiry:
                            line = encode_world(sequence, topology, commands)
                            if self.stream.write(line) != len(line):
                                raise serial.SerialTimeoutException("Incomplete World State write")
                            sent_at = time.perf_counter()
                            with self.lock:
                                if len(self.sent) >= 1024:
                                    self.overflow = True
                                else:
                                    self.sent.append((sent_at, self.session, sequence, generated, commands))
                            self.log("TX", sequence=sequence, topology=topology, movements=commands,
                                     generated=generated, session=self.session)
                        sent_revision = revision
                    self.stop.wait(.002)
                except (serial.SerialException, OSError) as error:
                    self.log("MODEM_LOST", error=str(error))
                    self.stream.close()
                    self.stream = None
                    with self.lock:
                        self.connected = False
                        self.error = str(error)
                    retry_at = time.perf_counter() + self.reconnect_seconds
        except Exception as error:
            self.log("MODEM_WORKER_FAILED", error=str(error))
            with self.lock:
                self.connected = False
                self.error = str(error)
                self.overflow = True
        finally:
            if self.stream:
                self.stream.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
