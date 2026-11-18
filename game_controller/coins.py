"""NFC arrival scoring and asynchronous, atomic score persistence."""

import configparser
import math
from pathlib import Path
import queue
import tempfile
import threading
import time


class CoinGame:
    def __init__(self, tags, totals=(0, 0, 0, 0), *, silver_points=1,
                 gold_points=10, respawn_seconds=120, resolver=None):
        if any(type(value) is not int or value < 0
               for value in (*totals, silver_points, gold_points)) or len(totals) != 4:
            raise ValueError("Coin totals and point values must be nonnegative integers")
        if not math.isfinite(respawn_seconds) or respawn_seconds <= 0:
            raise ValueError("Coin respawn_seconds must be finite and positive")
        self.tags = tags
        self.resolver = resolver
        self.totals = list(totals)
        self.points = (silver_points, gold_points)
        self.respawn_seconds = respawn_seconds
        self.last_uid = [None] * 4
        self.cooldowns = {}
        self.by_uid = {}
        if resolver is None:
            for number, tag in tags.items():
                uid = tag["nfc_uid"]
                if not uid:
                    continue
                if uid in self.by_uid:
                    raise ValueError(f"Duplicate NFC UID: {uid}")
                self.by_uid[uid] = number

    def observe(self, car, uid, now):
        """Process accepted telemetry in both modes. No-read never means departure."""
        index = car - 1
        match = self.resolver.lookup(uid) if self.resolver is not None else None
        if match is not None and match.kind in ("ignored", "no_read"):
            return None
        if not uid or uid == self.last_uid[index]:
            return None
        self.last_uid[index] = uid
        number = match.tid if match is not None else self.by_uid.get(uid)
        if number is None:
            return None
        tag = self.tags[number]
        if tag["track"] != str(car) or now < self.cooldowns.get(number, -math.inf):
            return None
        points = self.points[bool(tag.get("video_id"))]
        self.totals[index] += points
        self.cooldowns[number] = now + self.respawn_seconds
        return {"car": car, "tid": tag["tid"], "points": points,
                "total": self.totals[index]}

    def hidden(self, car, now):
        self.cooldowns = {tag: until for tag, until in self.cooldowns.items() if now < until}
        return tuple(sorted(tag for tag in self.cooldowns
                            if self.tags[tag]["track"] == str(car)))


class CoinStore:
    """Coalesce saves; report failures for logging and keep accepting snapshots."""

    def __init__(self, path):
        self.path = Path(path)
        parser = configparser.ConfigParser(interpolation=None)
        self.totals = (0, 0, 0, 0)
        if self.path.exists():
            with self.path.open(encoding="utf-8") as source:
                parser.read_file(source)
            self.totals = tuple(parser.getint("coins", f"car_{car}") for car in range(1, 5))
            if any(value < 0 for value in self.totals):
                raise ValueError("Saved coin totals must be nonnegative")
        self.pending = queue.Queue(maxsize=1)
        self.error = None
        self.failures = queue.SimpleQueue()
        self.thread = threading.Thread(target=self._run, name="coin-store", daemon=True)
        self.thread.start()
        self.submit(self.totals)

    def submit(self, totals):
        snapshot = tuple(totals)
        try:
            self.pending.get_nowait()
        except queue.Empty:
            pass
        self.pending.put_nowait(snapshot)

    def drain_errors(self):
        """Return each failed save once, for logging on the application thread."""
        failures = []
        while True:
            try:
                failures.append(self.failures.get_nowait())
            except queue.Empty:
                return failures

    def _run(self):
        while True:
            totals = self.pending.get()
            if totals is None:
                return
            try:
                self._save(totals)
                self.error = None
            except Exception as error:
                self.error = error
                self.failures.put((str(error), totals))
                # Skip this save; later snapshots still include all earned points.

    def _save(self, totals):
        parser = configparser.ConfigParser(interpolation=None)
        parser["coins"] = {f"car_{car}": str(total)
                           for car, total in enumerate(totals, 1)}
        # Unique names avoid sharing a staging file with another writer.
        # Close it before replace: Windows cannot rename an open file.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                         prefix=self.path.name + ".", suffix=".tmp",
                                         delete=False) as target:
            temporary = Path(target.name)
            parser.write(target)
        # Editors, antivirus and other readers can briefly deny replacement
        # on Windows. Retry here, never on the game/control thread.
        for attempt in range(21):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                if attempt == 20:
                    # Keep the complete snapshot for manual recovery.
                    raise
                time.sleep(.1)

    def close(self):
        while self.thread.is_alive():
            try:
                self.pending.put(None, timeout=.1)
                break
            except queue.Full:
                continue
        self.thread.join()
