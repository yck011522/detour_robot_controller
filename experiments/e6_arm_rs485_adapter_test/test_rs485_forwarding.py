"""Measure bidirectional latency and throughput through the UR tool-RS485 forwarder."""

import argparse
import configparser
from datetime import datetime
import math
from pathlib import Path
import socket
import statistics
import sys
import time

import serial

EXPERIMENT_DIRECTORY = Path(__file__).resolve().parent
PROJECT_DIRECTORY = EXPERIMENT_DIRECTORY.parents[1]
RESULTS_DIRECTORY = EXPERIMENT_DIRECTORY / "results"
ADDRESS_FILE = PROJECT_DIRECTORY / "config" / "address.ini"
ROBOT_TCP_PORT = 54321
LATENCY_PAYLOAD_SIZES = (1, 8, 32, 64)
TARGET_RATES = (0, 10, 25, 50, 100)


def parse_arguments():
    """Parse serial and bounded sweep settings."""
    parser = argparse.ArgumentParser(
        description="Measure bidirectional UR tool-RS485 latency and sending rate."
    )
    parser.add_argument("--port", default="COM18", help="USB RS485 adapter port (default: COM18)")
    parser.add_argument(
        "--baudrate", type=int, default=115200, help="RS485 baud rate (default: 115200)"
    )
    parser.add_argument(
        "--timeout", type=float, default=2.0, help="Per-read timeout in seconds (default: 2)"
    )
    parser.add_argument("--rounds", type=int, default=20, help="Samples per sweep point")
    arguments = parser.parse_args()
    if arguments.baudrate <= 0 or arguments.timeout <= 0 or arguments.rounds <= 0:
        parser.error("--baudrate, --timeout, and --rounds must be positive")
    return arguments


def read_exact(read, byte_count, timeout):
    """Read exactly byte_count bytes using a reader that accepts a byte count."""
    deadline = time.monotonic() + timeout
    received = bytearray()
    while len(received) < byte_count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"received {len(received)} of {byte_count} expected bytes")
        chunk = read(byte_count - len(received), remaining)
        if not chunk:
            raise TimeoutError(f"received {len(received)} of {byte_count} expected bytes")
        received.extend(chunk)
    return bytes(received)


def read_socket_exact(connection, byte_count, timeout):
    """Read a fixed number of bytes from the TCP stream."""

    def receive(count, remaining):
        connection.settimeout(remaining)
        try:
            return connection.recv(count)
        except socket.timeout:
            return b""

    return read_exact(receive, byte_count, timeout)


def read_serial_exact(adapter, byte_count, timeout):
    """Read a fixed number of bytes from the serial stream."""
    original_timeout = adapter.timeout

    def receive(count, remaining):
        adapter.timeout = remaining
        return adapter.read(count)

    try:
        return read_exact(receive, byte_count, timeout)
    finally:
        adapter.timeout = original_timeout


def payload(sequence, size):
    """Build a reproducible printable test payload of the requested byte size."""
    marker = f"E6{sequence:06d}".encode("ascii")
    return (marker * math.ceil(size / len(marker)))[:size]


def percentile(values, fraction):
    """Return a nearest-rank percentile from a non-empty sequence."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def report_stats(log, label, latencies, passed, total, elapsed=None):
    """Log latency, exact-match reliability, and optional achieved rate."""
    latency_ms = [value * 1000 for value in latencies]
    message = (
        f"{label}: matched={passed}/{total}, "
        f"latency_ms median/p95/max="
        f"{statistics.median(latency_ms):.3f}/"
        f"{percentile(latency_ms, 0.95):.3f}/"
        f"{max(latency_ms):.3f}"
    )
    if elapsed is not None:
        message += f", achieved_rate={total / elapsed:.2f} frames/s"
    log(message)
    return passed == total


def run_latency_sweep(log, direction, send, receive, rounds, timeout):
    """Measure per-payload forwarding latency and exact-byte reliability."""
    all_matched = True
    for size in LATENCY_PAYLOAD_SIZES:
        latencies = []
        matched = 0
        for sample in range(rounds):
            expected = payload(sample, size)
            started = time.perf_counter()
            send(expected)
            actual = receive(size, timeout)
            latencies.append(time.perf_counter() - started)
            matched += actual == expected
        all_matched &= report_stats(
            log, f"{direction} latency size={size} bytes", latencies, matched, rounds
        )
    return all_matched


def run_rate_sweep(log, direction, send, receive, rounds, timeout):
    """Pace short test frames at increasing targets and measure delivered rate."""
    size = 8
    all_matched = True
    for target_rate in TARGET_RATES:
        interval = 0 if target_rate == 0 else 1 / target_rate
        latencies = []
        matched = 0
        started = time.perf_counter()
        for sample in range(rounds):
            expected = payload(100000 + sample, size)
            transaction_started = time.perf_counter()
            send(expected)
            actual = receive(size, timeout)
            latencies.append(time.perf_counter() - transaction_started)
            matched += actual == expected
            if interval and sample + 1 < rounds:
                time.sleep(max(0, interval - latencies[-1]))
        elapsed = time.perf_counter() - started
        all_matched &= report_stats(
            log,
            f"{direction} target_rate={target_rate or 'max'} frames/s",
            latencies,
            matched,
            rounds,
            elapsed,
        )
    return all_matched


def main():
    """Run bounded bidirectional sweeps and write a timestamped result log."""
    arguments = parse_arguments()
    address_configuration = configparser.ConfigParser()
    if not address_configuration.read(ADDRESS_FILE):
        raise FileNotFoundError(f"Robot address configuration not found: {ADDRESS_FILE}")
    robot_host = address_configuration["robotic_arm"]["host"]

    RESULTS_DIRECTORY.mkdir(exist_ok=True)
    started_at = datetime.now().astimezone()
    log_path = RESULTS_DIRECTORY / started_at.strftime("rs485_test_%Y-%m-%d_%H-%M-%S.log")
    log_lines = [
        f"Started: {started_at.isoformat(timespec='seconds')}",
        f"Robot forwarder: {robot_host}:{ROBOT_TCP_PORT}",
        f"USB RS485 adapter: {arguments.port}",
        f"Serial settings: {arguments.baudrate} baud, 8 data bits, no parity, 1 stop bit",
        f"Rounds per sweep point: {arguments.rounds}",
        "Test data: short printable E6 markers; ensure the attached tool ignores them",
    ]

    def log(message):
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} | {message}"
        print(line, flush=True)
        log_lines.append(line)

    try:
        all_matched = True
        log("Opening serial adapter and connecting to the URCap TCP forwarder")
        with serial.Serial(
            arguments.port,
            baudrate=arguments.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=arguments.timeout,
            write_timeout=arguments.timeout,
        ) as adapter:
            with socket.create_connection(
                (robot_host, ROBOT_TCP_PORT), timeout=arguments.timeout
            ) as connection:
                adapter.reset_input_buffer()
                log("Latency sweep: URCap TCP -> tool RS485 -> USB adapter")
                all_matched &= run_latency_sweep(
                    log,
                    "TCP->adapter",
                    connection.sendall,
                    lambda count, timeout: read_serial_exact(adapter, count, timeout),
                    arguments.rounds,
                    arguments.timeout,
                )

                adapter.reset_input_buffer()
                log("Latency sweep: USB adapter -> tool RS485 -> URCap TCP")
                all_matched &= run_latency_sweep(
                    log,
                    "adapter->TCP",
                    lambda data: (adapter.write(data), adapter.flush()),
                    lambda count, timeout: read_socket_exact(connection, count, timeout),
                    arguments.rounds,
                    arguments.timeout,
                )

                adapter.reset_input_buffer()
                log("Sending-rate sweep: URCap TCP -> tool RS485 -> USB adapter")
                all_matched &= run_rate_sweep(
                    log,
                    "TCP->adapter",
                    connection.sendall,
                    lambda count, timeout: read_serial_exact(adapter, count, timeout),
                    arguments.rounds,
                    arguments.timeout,
                )

                adapter.reset_input_buffer()
                log("Sending-rate sweep: USB adapter -> tool RS485 -> URCap TCP")
                all_matched &= run_rate_sweep(
                    log,
                    "adapter->TCP",
                    lambda data: (adapter.write(data), adapter.flush()),
                    lambda count, timeout: read_socket_exact(connection, count, timeout),
                    arguments.rounds,
                    arguments.timeout,
                )

        result = "PASS" if all_matched else "FAIL (byte mismatches)"
        log(f"All sweep points completed: {result}")
        return 0 if all_matched else 1
    except (OSError, serial.SerialException) as error:
        log(f"Test failed with an I/O error: {error}")
        return 1
    finally:
        log_lines.append(f"Log file: {log_path}")
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        print(f"Log saved to {log_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
