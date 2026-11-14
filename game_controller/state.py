"""Accepted telemetry, driving gates and PC intersection filtering."""

from dataclasses import dataclass, field

from .protocol import AUTO_STOP, movement
from .navigation import Navigation


@dataclass
class Car:
    # Reservation metadata; never a driving permission or connectivity flag.
    present: bool = True
    packet: object = None
    received: float = 0
    session: int = 0
    echo_at: float = 0
    echo_generated: float = -1
    echo_age: float = 0
    telemetry_count: int = 0
    duplicates: int = 0
    stale: int = 0
    missing: int = 0
    resets: int = 0
    uid: str = ""
    echo_valid: bool = False
    epoch: float = 0
    zero_echo_armed: bool = False
    last_reason: str = ""
    recent: list = field(default_factory=list)
    navigation: Navigation = field(default_factory=Navigation)

    def accept(self, packet, now, session, history, stale_seconds):
        """Accept ordered packets; gaps/resets invalidate echo confidence.

        Without a session token reboot detection is necessarily heuristic. A
        discontinuity while feedback is live is rejected; after an outage a new
        baseline is accepted and must echo a newly generated local snapshot.
        """
        gap = self.packet is not None and now - self.received > stale_seconds
        new_epoch = self.packet is None or session != self.session or gap
        if self.packet is not None and not new_epoch:
            delta = (packet.sequence - self.packet.sequence) & 65535
            if delta == 0:
                self.duplicates += 1
                return "duplicate"
            plausible = max(20, int((now - self.received) * 100) + 20)
            if delta >= 32768 or delta > plausible:
                self.stale += 1
                return "stale"
            self.missing += delta - 1
        if new_epoch:
            self.epoch = now
            self.echo_at = 0
            self.echo_valid = False
            self.echo_generated = -1
            self.zero_echo_armed = False
            if self.packet is not None:
                self.resets += 1
        self.packet, self.received, self.session = packet, now, session
        self.telemetry_count += 1
        self.recent.append(now)
        self.recent = [t for t in self.recent if now - t <= 5]
        record = history.get(packet.echo)
        if packet.echo != 0:
            self.zero_echo_armed = True
        if record and record[0] == session and record[1] >= self.epoch and (packet.echo or self.zero_echo_armed):
            _, generated, sent = record
            if generated > self.echo_generated:
                self.echo_generated = generated
                self.echo_at = now
                self.echo_age = max(0, now - sent)
                self.echo_valid = True
        uid = packet.uid.hex() if packet.uid != b"\x00" * 4 else ""
        changed = uid != self.uid
        self.uid = uid
        return "uid" if changed else "accepted"

    def feedback_ok(self, now, threshold):
        return self.packet is not None and now - self.received <= threshold

    def echo_ok(self, now, threshold):
        return self.echo_valid and now - self.echo_at <= threshold

    def warning(self):
        """Expose reported state without claiming an unreported firmware latch."""
        if self.packet and self.packet.controller == 15:
            if self.packet.sensors in (0, 255):
                return "STOPPED/no usable line: possible post-NFC forward/line interlock"
            return "STOPPED: post-NFC recovery may require a forward command"
        return ""

    def command(self, raw, now, telemetry_timeout, echo_timeout, modem_ok, joystick_ok,
                admin, selected, car_id, keyboard, stop_only=False):
        """Fresh held input resumes immediately; no release-before-resume latch."""
        reason = ""
        if stop_only:
            reason = "Stop-only verification"
        elif not modem_ok:
            reason = "Modem unavailable"
        elif not joystick_ok:
            reason = "Gamepad unavailable"
        elif not self.feedback_ok(now, telemetry_timeout):
            reason = "Car feedback stale"
        elif not self.echo_ok(now, echo_timeout):
            reason = "Command echo stale"
        elif self.packet.controller not in (0, 1, 15):
            reason = "Car protection/recovery"
        elif admin and selected != car_id:
            reason = "Admin controls another car"
        self.last_reason = reason
        if admin:
            # Keep player context aging without letting keyboard input choose
            # or overwrite a player branch. Admin commands bypass tag filtering.
            self.navigation.advance(now)
            edge = ""
        else:
            edge = self.navigation.steering(raw[1], raw[2], now, choose=not reason)
        if reason:
            return AUTO_STOP
        if admin:
            return movement(keyboard[0], keyboard[1], keyboard[2],
                            manual=keyboard[4], backward=keyboard[3])
        return movement(raw[0], edge == "left", edge == "right")
