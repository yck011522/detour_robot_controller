"""PC-only split/merge steering with confirmed board-presence admission."""

from dataclasses import dataclass
import math


def validate_tags(tags):
    for tag in tags.values():
        name = tag["tid"]
        split, merge = tag.get("split", ""), tag.get("mergeedge", "")
        if split not in ("", "left", "right", "leftright"):
            raise ValueError(f"{name}: invalid split {split!r}")
        if merge not in ("", "left", "right"):
            raise ValueError(f"{name}: invalid mergeedge {merge!r}")
        if split and merge:
            raise ValueError(f"{name}: split and mergeedge conflict")
        if tag.get("safeexit", "") not in ("", "left", "right"):
            raise ValueError(f"{name}: invalid safeexit")
        if tag.get("reservezone", "") not in ("", "A", "B", "C", "D"):
            raise ValueError(f"{name}: invalid reservezone")
        if tag.get("inzone", "") not in ("", "A", "B", "C", "D"):
            raise ValueError(f"{name}: invalid inzone")
        if tag.get("reservezone") and (not split or tag.get("safeexit") not in ("left", "right")):
            raise ValueError(f"{name}: zone approach requires split and safeexit")


def split_edge(split, left, right):
    if left and right:
        left = right = False
    if split == "left":
        return "left" if left else "right"
    return "right" if right else "left"


@dataclass
class Navigation:
    lock_age_ms: float = 100
    uid: str = ""
    tid: str = ""
    split: str = ""
    mergeedge: str = ""
    safeexit: str = ""
    reservezone: str = ""
    inzone: str = ""
    edge: str = ""
    locked: bool = False
    age_ms: int = 0
    received: float = 0
    board_zones: frozenset = frozenset()

    def __post_init__(self):
        if not math.isfinite(self.lock_age_ms) or self.lock_age_ms <= 0:
            raise ValueError("split_lock_age_ms must be finite and positive")

    def observe(self, uid, age_ms, now, tag=None):
        """Only called for ordered, accepted telemetry. Empty UID is no-read."""
        if not uid:
            self.advance(now)
            return
        if uid != self.uid:
            tag = tag or {}
            self.uid, self.tid = uid, tag.get("tid", "unknown")
            self.split = tag.get("split", "")
            self.mergeedge = tag.get("mergeedge", "")
            self.safeexit = tag.get("safeexit", "")
            self.reservezone = tag.get("reservezone", "")
            self.inzone = tag.get("inzone", "")
            self.edge, self.locked = "", False
        self.age_ms, self.received = age_ms, now
        self.advance(now)

    def advance(self, now):
        # Advance the last reported age between 20 Hz telemetry samples.
        age = self.age_ms + max(0, now - self.received) * 1000
        if self.split and age > self.lock_age_ms:
            self.locked = True

    def steering(self, left, right, now, *, choose=True):
        self.advance(now)
        if self.mergeedge:
            self.edge = self.mergeedge
            return self.edge
        if not self.split:
            return ""
        if self.locked and self.edge:
            return self.edge
        if self.reservezone and self.reservezone not in self.board_zones:
            self.edge = self.safeexit
        elif not self.edge:
            # A first observation already older than the threshold has no prior
            # selection to freeze: use the documented neutral default.
            self.edge = split_edge(self.split, left if choose and not self.locked else False,
                                   right if choose and not self.locked else False)
        elif choose and not self.locked:
            self.edge = split_edge(self.split, left, right)
        return self.edge

    @property
    def selected_exit(self):
        if not self.split or not self.edge:
            return ""
        if (self.split, self.edge) in (("left", "right"), ("right", "left")):
            return "straight"
        return self.edge
