"""E10: bounded no-load tool test through URCap TCP or a USB RS-485 adapter.

No arm trajectory, force mode, firmware changes or persistent tool SET commands.
Use --run to authorize power/brake operations and four short motor runs.
"""

import argparse
import configparser
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import socket
import struct
import time


ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
HK = timezone(timedelta(hours=8))


class Log:
    """Flush timestamped requests, replies and outcomes immediately to disk."""

    def __init__(self, label):
        RESULTS.mkdir(exist_ok=True)
        self.path = RESULTS / f"{datetime.now(HK):%Y%m%d_%H%M%S_%f}_{label}.log"
        self.file = self.path.open("x", encoding="utf-8")
        self.started = time.monotonic()
        self.write("START", outcome="INCOMPLETE", label=label)

    def write(self, event, **data):
        record = {"time": datetime.now(HK).isoformat(timespec="milliseconds"),
                  "elapsed_s": round(time.monotonic() - self.started, 4),
                  "event": event, **data}
        line = json.dumps(record)
        self.file.write(line + "\n")
        self.file.flush()
        print(line, flush=True)

    def close(self):
        self.file.close()


class LineClient:
    """One serialized ASCII request/reply stream with finite receive deadlines."""

    def __init__(self, host, port, log, timeout=0.4):
        self.log, self.port, self.timeout = log, port, timeout
        self.socket = socket.create_connection((host, port), timeout=2)
        self.socket.settimeout(timeout)
        self.buffer = bytearray()

    def line(self, deadline):
        """Assemble split TCP reads without accepting unbounded lines or waits."""
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Response deadline on port {self.port}")
            self.socket.settimeout(remaining)
            part = self.socket.recv(2048)
            if not part:
                raise ConnectionError(f"Connection closed on port {self.port}")
            self.buffer.extend(part)
            if len(self.buffer) > 4096:
                raise RuntimeError("Unexpected overlong response")
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        return line.rstrip(b"\r").decode("ascii", errors="strict")

    def command(self, command, expected=None, timeout=None):
        """Never retry a motor command: a timeout leaves its acceptance unknown."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        self.log.write("TX", port=self.port, command=command)
        self.socket.settimeout(max(.001, deadline - time.monotonic()))
        self.socket.sendall((command + "\n").encode("ascii"))
        reply = self.line(deadline)
        self.log.write("RX", port=self.port, command=command, reply=reply)
        if reply.startswith("ERR ") or reply.lower().startswith(("failed", "unable", "command is not allowed")):
            raise RuntimeError(f"{command}: {reply}")
        if expected is not None and reply != expected:
            raise RuntimeError(f"{command}: expected {expected!r}, got {reply!r}")
        return reply

    def close(self):
        self.socket.close()


class SerialTransport:
    """Expose the byte-stream operations shared by TCP and pyserial."""

    def __init__(self, port):
        import serial

        self.serial = serial.Serial(None, baudrate=115200, bytesize=8,
                                    parity="N", stopbits=1, timeout=0,
                                    write_timeout=.2, xonxoff=False, rtscts=False,
                                    dsrdtr=False)
        # Match the working monitor: manual DTR high, RTS low. Set these before
        # opening so the driver receives the intended control-line states.
        self.serial.dtr = True
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.timeout = .4

    def settimeout(self, seconds):
        # pyserial on Windows re-applies SetCommState when timeouts change.
        # Keep the driver configuration stable while a fast reply is arriving;
        # implement the receive deadline here instead.
        self.timeout = seconds

    def sendall(self, data):
        if self.serial.write(data) != len(data):
            raise TimeoutError("Incomplete serial write")

    def recv(self, count):
        # Waiting for all 2048 bytes would add a full timeout to every reply.
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            available = self.serial.in_waiting
            if available:
                data = self.serial.read(min(count, available))
                if data:
                    return data
            time.sleep(.001)
        raise TimeoutError("Serial reply timeout")

    def close(self):
        self.serial.close()


class SerialClient(LineClient):
    """Reuse identical framing, logging and protocol checks on the USB path."""

    def __init__(self, port, log):
        self.log, self.port, self.timeout = log, port, .4
        self.socket = SerialTransport(port)
        self.buffer = bytearray()
        # Earlier interrupted writes can leave a partial line in the tool.
        # Finish it before the first request; discard only startup/sync replies.
        time.sleep(.5)
        self.socket.sendall(b"\n")
        time.sleep(.1)
        pending = self.socket.serial.read(self.socket.serial.in_waiting)
        if pending:
            log.write("SERIAL_SYNC_DISCARD", raw_hex=pending.hex())


def host_from_config():
    """Use the same robot address as E7 and E6."""
    config = configparser.ConfigParser(interpolation=None)
    with (ROOT / "config" / "address.ini").open(encoding="utf-8") as source:
        config.read_file(source)
    return config["robotic_arm"]["host"]


def dashboard(host, log):
    """Connect without uploading an RTDE control script or starting a program."""
    client = LineClient(host, 29999, log, timeout=3)
    try:
        greeting = client.line(time.monotonic() + 3)
        log.write("DASHBOARD_GREETING", reply=greeting)
        if "Universal Robots Dashboard Server" not in greeting:
            raise RuntimeError("Unexpected Dashboard greeting")
        return client
    except BaseException:
        client.close()
        raise


def wait_mode(client, mode, seconds, polls=3):
    """Use E7's consecutive-mode readiness check, bounded by a total deadline."""
    deadline, matched = time.monotonic() + seconds, 0
    while time.monotonic() < deadline:
        reply = client.command("robotmode", timeout=min(3, deadline - time.monotonic()))
        matched = matched + 1 if reply.upper() == f"ROBOTMODE: {mode}" else 0
        if matched >= polls:
            return
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"Robot did not reach stable {mode}")


def prepare_robot(client):
    """Require a stopped program and normal safety state before power/brake changes."""
    client.command("is in remote control", "true")
    client.command("safetystatus", "Safetystatus: NORMAL")
    client.command("running", "Program running: false")
    mode = client.command("robotmode").partition(":")[2].strip()
    if mode not in ("POWER_OFF", "POWER_ON", "BOOTING", "IDLE", "RUNNING"):
        raise RuntimeError(f"Unsupported initial robot mode: {mode}")
    if mode == "POWER_OFF":
        client.command("power on", "Powering on")
    if mode != "RUNNING":
        wait_mode(client, "IDLE", 30)
        client.command("brake release", "Brake releasing")
    wait_mode(client, "RUNNING", 60)
    client.command("running", "Program running: false")
    client.command("safetystatus", "Safetystatus: NORMAL")


def connect_tool(host, port, log):
    """Allow flange-powered firmware to boot, using harmless PING retries only."""
    deadline = time.monotonic() + 15
    while True:
        client = None
        try:
            # Give a newly accepted forwarder connection time to attach its UART.
            # Motor-command deadlines remain short and are set separately below.
            client = LineClient(host, port, log, timeout=3)
            client.command("PING", "OK PONG")
            version = client.command("VERSION")
            if not version.startswith("OK v"):
                raise RuntimeError(f"Unexpected tool version: {version}")
            client.timeout = .4  # Short operational deadlines after boot/handshake.
            return client
        except (OSError, RuntimeError) as error:
            if client:
                client.close()
            if time.monotonic() >= deadline:
                raise RuntimeError("No valid tool handshake through URCap; check flange power, "
                                   "115200 8N1 and forwarder port") from error
            log.write("WAIT_TOOL_BOOT", error=str(error))
            time.sleep(.5)


def status(tool, timeout=None):
    """Parse the current five-token STATUS response, not obsolete RUN examples."""
    reply = tool.command("STATUS", timeout=timeout)
    words = reply.split()
    states = {"IDLE", "TIGHTENING", "LOOSENING", "STALLED"}
    if len(words) != 5 or words[0] != "OK" or any(s not in states for s in words[1:3]):
        raise RuntimeError(f"Malformed STATUS: {reply}")
    current, pwm = map(int, words[3:])
    # Boot-offset subtraction can produce small negative readings while idle.
    # Preserve the signed measurement rather than clamping or rejecting noise.
    if not -1500 <= current <= 1500 or not 0 <= pwm <= 100:
        raise RuntimeError(f"Invalid STATUS values: {reply}")
    return {"states": words[1:3], "current_ma": current, "pwm_percent": pwm}


def stop_and_verify(tool):
    """Confirm both motors stopped, not just that STOP was acknowledged."""
    tool.command("STOP", "OK STOP")
    for _ in range(3):
        sample = status(tool)
        if sample["states"] != ["IDLE", "IDLE"] or sample["pwm_percent"] != 0:
            raise RuntimeError(f"STOP not confirmed: {sample}")
        time.sleep(.05)


def tool_io_state(host):
    """Read tool power through a small RTDE output-only recipe (no input writes)."""
    with socket.create_connection((host, 30004), timeout=2) as connection:
        connection.settimeout(2)

        def exact(count):
            """Read a complete RTDE frame segment or fail on disconnect."""
            data = bytearray()
            while len(data) < count:
                part = connection.recv(count - len(data))
                if not part:
                    raise ConnectionError("RTDE diagnostic connection closed")
                data.extend(part)
            return bytes(data)

        def receive():
            """Decode the RTDE network-order length/type header."""
            size, kind = struct.unpack(">HB", exact(3))
            if size < 3:
                raise RuntimeError("Invalid RTDE packet size")
            return kind, exact(size - 3)

        def exchange(kind, payload=b""):
            """Ignore unrelated RTDE messages while awaiting a bounded reply."""
            connection.sendall(struct.pack(">HB", len(payload) + 3, kind) + payload)
            for _ in range(10):
                returned, data = receive()
                if returned == kind:
                    return data
            raise RuntimeError("Expected RTDE reply not received")

        if exchange(86, struct.pack(">H", 2)) != b"\x01":
            raise RuntimeError("RTDE protocol version 2 was not accepted")
        recipe = exchange(79, struct.pack(">d", 1.0) +
                          b"tool_output_voltage,tool_output_current,tool_mode")
        if recipe[1:] != b"INT32,DOUBLE,UINT32" or exchange(83) != b"\x01":
            raise RuntimeError("Tool RTDE diagnostic recipe unavailable")
        for _ in range(10):
            kind, data = receive()
            if kind == 85 and data[0] == recipe[0]:
                voltage, current, mode = struct.unpack(">idI", data[1:])
                exchange(80)
                return {"voltage_v": voltage, "current_ma": current, "mode": mode}
        raise TimeoutError("No tool RTDE sample")


def configure_tool_io(host, log, voltage=24, serial=True):
    """Send only non-motion tool-I/O statements; readback verifies voltage."""
    if voltage not in (0, 12, 24):
        raise ValueError("Unsupported tool voltage")
    statements = [f"  set_tool_voltage({voltage})"]
    if serial:
        statements.append("  set_tool_communication(True, 115200, 0, 1, 1.5, 3.5)")
    script = "sec e10_tool_io():\n" + "\n".join(statements) + "\nend\n"
    log.write("TOOL_IO_SCRIPT", script=script)
    with socket.create_connection((host, 30002), timeout=2) as connection:
        connection.sendall(script.encode("ascii"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = tool_io_state(host)
        log.write("TOOL_IO_READBACK", **state)
        if state["voltage_v"] == voltage:
            return
        time.sleep(.2)
    raise RuntimeError(f"Tool voltage did not reach {voltage} V")


def run_motor(tool, motor, action, seconds, log):
    """A bounded motor operation; unexpected stall fails E10 without retrying."""
    stop_and_verify(tool)
    deadline = time.monotonic() + seconds
    expected = ["IDLE", "IDLE"]
    expected[motor - 1] = "TIGHTENING" if action == "TIGHTEN" else "LOOSENING"
    samples, powered = 0, False
    try:
        command = f"{action} M{motor}"
        tool.command(command, f"OK {command}", timeout=min(.4, max(.001, deadline-time.monotonic())))
        while (remaining := deadline - time.monotonic()) > .015:
            sample = status(tool, timeout=min(.3, remaining))
            log.write("MOTOR_SAMPLE", motor=motor, action=action, **sample)
            samples += 1
            powered |= sample["pwm_percent"] > 0
            if "STALLED" in sample["states"]:
                raise RuntimeError(f"Unexpected stall during no-load test: {sample}")
            if sample["states"] != expected:
                raise RuntimeError(f"Unexpected motor state: {sample}, expected {expected}")
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        if not powered or samples < 2:
            raise RuntimeError("Insufficient running/PWM evidence")
    finally:
        stop_and_verify(tool)
    log.write("STAGE_PASS", motor=motor, action=action, samples=samples,
              commanded_duration_s=seconds, observed_stall=False)


def power_off(host, log):
    """Independent Dashboard cleanup still works after tool/TCP reply failure."""
    client = dashboard(host, log)
    try:
        try:
            client.command("stop", "Stopped")
        except Exception as error:
            # A rejected program-stop must not prevent the power-off attempt.
            log.write("DASHBOARD_STOP_FAILED", error=str(error))
        client.command("power off", "Powering off")
        wait_mode(client, "POWER_OFF", 30, polls=1)
        log.write("POWER_OFF_CONFIRMED")
    finally:
        client.close()


def main():
    """Run the authorized no-load sequence and always attempt stop/power-off cleanup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="perform real power/brake and motor commands")
    parser.add_argument("--seconds", type=float, default=2, help="each no-load run, 0.5 to 3 seconds")
    parser.add_argument("--port", type=int, default=54321, help="existing URCap TCP forwarder port")
    parser.add_argument("--serial-port", help="USB RS-485 adapter, e.g. COM18, instead of URCap TCP")
    parser.add_argument("--keep-powered", action="store_true",
                        help="leave robot powered after PASS and verified motor stop; failures power off")
    parser.add_argument("--configure-tool", action="store_true",
                        help="explicitly set 24 V and 115200 8N1 with a non-motion secondary script")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    if not math.isfinite(args.seconds) or not .5 <= args.seconds <= 3:
        parser.error("--seconds must be between 0.5 and 3")
    if args.serial_port and args.configure_tool:
        parser.error("--configure-tool configures the UR RS-485 path; omit it for USB")
    host, log = host_from_config(), Log("usb_no_load" if args.serial_port else "no_load")
    control = tool = receiver = None
    cleanup_required, outcome = False, "FAIL"
    restore_voltage = None
    stop_confirmed = True
    try:
        control = dashboard(host, log)
        # Check before claiming ownership of the power-off lifecycle.
        control.command("is in remote control", "true")
        control.command("safetystatus", "Safetystatus: NORMAL")
        control.command("running", "Program running: false")
        cleanup_required = True
        prepare_robot(control)
        state = tool_io_state(host)
        log.write("INITIAL_TOOL_IO", **state)
        if args.configure_tool:
            restore_voltage = state["voltage_v"]
            configure_tool_io(host, log)
        elif state["voltage_v"] != 24:
            raise RuntimeError("Tool is not configured for 24 V; use --configure-tool to enable the required I/O")
        from rtde_receive import RTDEReceiveInterface

        receiver = RTDEReceiveInterface(host)
        reference = list(receiver.getActualQ())
        if len(reference) != 6 or not all(math.isfinite(q) for q in reference):
            raise RuntimeError("Invalid robot joint feedback")
        if args.serial_port:
            tool = SerialClient(args.serial_port, log)
            # Keep one USB connection open while allowing flange-powered boot.
            # PING is harmless to repeat; no motor command is retried.
            deadline = time.monotonic() + 15
            while True:
                try:
                    tool.command("PING", "OK PONG", timeout=3)
                    break
                except TimeoutError as error:
                    if time.monotonic() >= deadline:
                        raise
                    log.write("WAIT_TOOL_BOOT", error=str(error))
                    time.sleep(.5)
            version = tool.command("VERSION", timeout=3)
            if not version.startswith("OK v"):
                raise RuntimeError(f"Unexpected tool version: {version}")
        else:
            tool = connect_tool(host, args.port, log)
        initial = status(tool)
        log.write("INITIAL_TOOL_STATUS", **initial)
        if initial["states"] != ["IDLE", "IDLE"]:
            raise RuntimeError("Tool was not initially idle; automatic test aborted")
        stop_and_verify(tool)
        for motor in (1, 2):
            tool.command(f"GET CONFIG M{motor}")  # Read only; never SET/RESET/SAVE.
        for motor in (1, 2):
            for action in ("TIGHTEN", "LOOSEN"):
                control.command("safetystatus", "Safetystatus: NORMAL")
                control.command("running", "Program running: false")
                run_motor(tool, motor, action, args.seconds, log)
                joints = list(receiver.getActualQ())
                drift = max(abs(q-r) for q, r in zip(joints, reference))
                log.write("ARM_STATIONARY_CHECK", max_joint_change_rad=drift)
                if len(joints) != 6 or not all(math.isfinite(q) for q in joints) or drift > .01:
                    raise RuntimeError("Unexpected arm joint change during tool-only test")
                time.sleep(.3)
        outcome = "PASS"
    except KeyboardInterrupt:
        outcome = "INTERRUPTED"
        log.write("INTERRUPTED")
    except Exception as error:
        log.write("FAILURE", error=str(error))
    finally:
        if tool:
            try:
                stop_and_verify(tool)
            except Exception as error:
                outcome = "FAIL"
                stop_confirmed = False
                log.write("TOOL_STOP_UNCONFIRMED", error=str(error))
            finally:
                tool.close()
        if cleanup_required:
            # Never delay the independent power-off path with configuration
            # restoration when the tool's stop state is unknown.
            if restore_voltage is not None and stop_confirmed:
                try:
                    configure_tool_io(host, log, voltage=restore_voltage, serial=False)
                except Exception as error:
                    outcome = "FAIL"
                    log.write("TOOL_VOLTAGE_RESTORE_FAILED", error=str(error))
            if args.keep_powered and outcome == "PASS" and stop_confirmed:
                log.write("LEFT_POWERED", reason="requested; PASS and both motors confirmed stopped")
            else:
                try:
                    power_off(host, log)
                except Exception as error:
                    outcome = "FAIL"
                    log.write("POWER_OFF_UNCONFIRMED", error=str(error), operator_intervention=True)
        if receiver:
            receiver.disconnect()
        if control:
            control.close()
        log.write("RESULT", outcome=outcome,
                  scope="communication, commanded run states/PWM and confirmed stop; no attachment/stall proof")
        log.close()
        print(f"{outcome}: {log.path}", flush=True)
    return int(outcome != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
