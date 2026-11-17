"""E7's stable-IDLE/RUNNING Dashboard sequence, split across the game lifecycle."""

from contextlib import contextmanager
import time

from .step import Log
from .tool import LineClient, host_from_config


def wait_mode(client, mode, seconds, polls=3):
    deadline, matched = time.monotonic() + seconds, 0
    while (remaining := deadline - time.monotonic()) > 0:
        reply = client.command("robotmode", timeout=min(5, remaining))
        if time.monotonic() >= deadline:
            break
        matched = matched + 1 if reply.lower() == f"robotmode: {mode}".lower() else 0
        if matched >= polls:
            return
        time.sleep(max(0, min(1, deadline - time.monotonic())))
    raise TimeoutError(f"Robot did not reach stable {mode} within {seconds} seconds")


@contextmanager
def dashboard():
    log = Log()
    client = None
    try:
        client = LineClient(host_from_config(), 29999, log, timeout=5)
        greeting = client.line(time.monotonic() + 5)
        log.write("DASHBOARD_GREETING", reply=greeting)
        if "Universal Robots Dashboard Server" not in greeting:
            raise RuntimeError("Unexpected Dashboard greeting")
        yield client
    finally:
        try:
            if client:
                client.close()
        finally:
            log.file.close()


class ArmPower:
    def __init__(self):
        self.managed = False
        self.ready = False

    def start(self):
        if self.ready:
            return
        with dashboard() as client:
            client.command("is in remote control", "true")
            client.command("safetystatus", "Safetystatus: NORMAL")
            client.command("running", "Program running: false")
            mode = client.command("robotmode").partition(":")[2].strip().upper()
            if mode not in ("POWER_OFF", "POWER_ON", "BOOTING", "IDLE", "RUNNING"):
                raise RuntimeError(f"Cannot start from robot mode {mode!r}")
            self.managed = True
            if mode == "POWER_OFF":
                client.command("power on", "Powering on")
            if mode != "RUNNING":
                wait_mode(client, "IDLE", 15)
                client.command("brake release", "Brake releasing")
            wait_mode(client, "RUNNING", 60)
            self.ready = True

    def stop(self):
        if not self.managed:
            return
        self.ready = False
        # Reconnect independently of any failed RTDE/tool/startup connection.
        with dashboard() as client:
            try:
                client.command("stop", "Stopped")
            finally:
                client.command("power off", "Powering off")
                wait_mode(client, "POWER_OFF", 60, polls=1)
        self.managed = False
