"""Line-based ASCII client for the screwdriver tool through the URCap TCP forwarder."""

import socket
import time

from .settings import ROOT, read_ini


class CommandRejected(RuntimeError):
    """A complete error reply, distinct from an uncertain timeout or disconnect."""

    def __init__(self, command, reply):
        super().__init__(f"{command}: {reply}")
        self.command, self.reply = command, reply


class LineClient:
    """One serialized request/reply stream with finite receive deadlines."""

    def __init__(self, host, port, log, timeout=0.4):
        self.log, self.port, self.timeout = log, port, timeout
        self.socket = socket.create_connection((host, port), timeout=2)
        self.socket.settimeout(timeout)
        self.buffer = bytearray()
        self.first_request = True

    def line(self, deadline):
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
        """Retry one explicit unknown-command rejection; never retry uncertain delivery."""
        try:
            return self._command_once(command, expected, timeout)
        except CommandRejected as error:
            if error.reply != "ERR 1 unknown command":
                raise
            self.log.write("TOOL_COMMAND_RETRY", port=self.port, command=command,
                           reason=str(error), attempts_remaining=1)
            return self._command_once(command, expected, timeout)

    def _command_once(self, command, expected=None, timeout=None):
        seconds = self.timeout if timeout is None else timeout
        # The forwarder can accept TCP before the serial side is ready. Live
        # probes measured about one second to the first reply after reconnect.
        if self.first_request and timeout is None:
            seconds = max(seconds, 1.5)
        self.first_request = False
        deadline = time.monotonic() + seconds
        self.log.write("TX", port=self.port, command=command)
        self.socket.settimeout(max(.001, deadline - time.monotonic()))
        self.socket.sendall((command + "\n").encode("ascii"))
        reply = self.line(deadline)
        self.log.write("RX", port=self.port, command=command, reply=reply)
        if reply.startswith("ERR ") or reply.lower().startswith(("failed", "unable", "command is not allowed")):
            raise CommandRejected(command, reply)
        if expected is not None and reply != expected:
            raise RuntimeError(f"{command}: expected {expected!r}, got {reply!r}")
        return reply

    def close(self):
        self.socket.close()


def robot_config():
    return read_ini(ROOT / "config/address.ini")["robotic_arm"]


def host_from_config():
    return robot_config()["host"]


def tool_port():
    return int(robot_config()["tool_tcp_port"])


def status(tool, timeout=None):
    """Parse the five-token STATUS response."""
    reply = tool.command("STATUS", timeout=timeout)
    words = reply.split()
    states = {"IDLE", "TIGHTENING", "LOOSENING", "STALLED"}
    if len(words) != 5 or words[0] != "OK" or any(s not in states for s in words[1:3]):
        raise RuntimeError(f"Malformed STATUS: {reply}")
    current, pwm = map(int, words[3:])
    # Boot-offset subtraction can make idle readings slightly negative; keep the sign.
    if not -1500 <= current <= 1500 or not 0 <= pwm <= 100:
        raise RuntimeError(f"Invalid STATUS values: {reply}")
    return {"states": words[1:3], "current_ma": current, "pwm_percent": pwm}


def stop_tool(tool):
    tool.command("STOP", "OK STOP")
    sample = status(tool)
    if sample["states"] != ["IDLE", "IDLE"] or sample.get("pwm_percent", 0) != 0:
        raise RuntimeError("Tool STOP unconfirmed")
