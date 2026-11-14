"""Confirmed board inventory, startup questions and shared physical UID resolution."""

import configparser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile


BOARDS = ("P", "Q", "R", "S")
LOCATIONS = ("unassigned", "A", "B", "C", "D", "gripper", "spare")


def validate_arrangement(arrangement):
    if set(arrangement) != set(BOARDS):
        raise ValueError("Assign all four boards P, Q, R and S.")
    locations = list(arrangement.values())
    if any(location not in LOCATIONS[1:] for location in locations):
        raise ValueError("Assign every board to a zone, gripper or spare.")
    if len(set(locations)) != 4:
        raise ValueError("Two boards cannot occupy the same location.")
    if locations.count("spare") != 1:
        raise ValueError("Exactly one board must be spare; three must be active.")


def load_board_state(path):
    if not path.exists():
        return {"P": "unassigned", "Q": "unassigned", "R": "unassigned", "S": "spare"}
    parser = configparser.ConfigParser(interpolation=None)
    with path.open(encoding="utf-8-sig") as source:
        parser.read_file(source)
    result = {name: parser.get("boards", name) for name in BOARDS}
    validate_arrangement(result)
    return result


def save_board_state(path, arrangement):
    validate_arrangement(arrangement)
    parser = configparser.ConfigParser(interpolation=None)
    parser["boards"] = arrangement
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            target.write("# Last admin-confirmed board layout; confirm physically at each startup.\n")
            parser.write(target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class BoardStartup:
    """Main-thread form model; saving runs outside the control loop."""

    def __init__(self, path):
        self.path = Path(path)
        self.draft = load_board_state(self.path)
        self.selected = 0
        self.error = ""
        self.confirmed = False
        self.future = None
        self.saved = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="board-state")

    def cycle(self, direction):
        if self.future is None and not self.confirmed:
            board = BOARDS[self.selected]
            index = LOCATIONS.index(self.draft[board])
            self.draft[board] = LOCATIONS[(index + direction) % len(LOCATIONS)]
            self.error = ""

    def confirm(self):
        if self.future is not None or self.confirmed:
            return
        try:
            validate_arrangement(self.draft)
        except ValueError as error:
            self.error = str(error)
            return
        self.saved = dict(self.draft)
        self.error = ""
        self.future = self.executor.submit(save_board_state, self.path, self.saved)

    def poll(self):
        if self.future is None or not self.future.done():
            return False
        try:
            self.future.result()
        except Exception as error:
            self.error = f"Could not save board state: {error}"
            self.future = None
            return False
        self.future = None
        self.confirmed = True
        return True

    def snapshot(self):
        return {"active": not self.confirmed, "rows": tuple(self.draft.items()),
                "selected": self.selected, "saving": self.future is not None,
                "error": self.error}

    def close(self):
        self.executor.shutdown(wait=True)


def ask_boards_console(startup):
    """Headless diagnostics: collect confirmation before starting the live tick."""
    while True:
        print("Confirm physical board layout. Locations: A/B/C/D/gripper/spare; Q quits.")
        for board in BOARDS:
            current = startup.draft[board]
            answer = input(f"Board {board} [{current}]: ").strip()
            if answer.lower() == "q":
                raise KeyboardInterrupt
            if answer:
                startup.draft[board] = answer.upper() if answer.upper() in "ABCD" and len(answer) == 1 else answer.lower()
        try:
            validate_arrangement(startup.draft)
        except ValueError as error:
            print(error)
            continue
        if input(f"Confirm {startup.draft}? [y/N]: ").strip().lower() == "y":
            startup.confirm()
            return


@dataclass(frozen=True)
class TagMatch:
    kind: str
    tid: int | None = None


class TagResolver:
    """Resolve fixed/slot UIDs; known unused board tags are explicitly ignored."""

    def __init__(self, template, dynamic_path):
        parser = configparser.ConfigParser(interpolation=None)
        with Path(dynamic_path).open(encoding="utf-8-sig") as source:
            parser.read_file(source)
        self.board_uids = {}
        for board in BOARDS:
            for index in (1, 2, 3):
                name = f"{board.lower()}{index}"
                value = parser.get("tags", name).strip().lower()
                if not re.fullmatch(r"[0-9a-f]{8}", value):
                    raise ValueError(f"Invalid NFC registration for {name}")
                if value in self.board_uids.values():
                    raise ValueError(f"Duplicate board NFC UID: {value}")
                self.board_uids[name] = value
        self.template = template
        self.tags = {}
        self.by_uid = {}
        self.ignored_uids = set()
        self.assign(None)

    def assign(self, arrangement):
        if arrangement is not None:
            validate_arrangement(arrangement)
        zone_boards = {zone.lower(): board.lower() for board, zone in (arrangement or {}).items()
                       if zone in ("A", "B", "C", "D")}
        tags, by_uid, slots = {}, {}, set()
        all_board_uids = set(self.board_uids.values())
        for number, attributes in self.template.items():
            tag = dict(attributes)
            value = tag["nfc_uid"].strip().lower()
            slot = re.fullmatch(r"<([abcd])([123])>", value)
            if slot:
                if value in slots:
                    raise ValueError(f"Duplicate slot reference: {value}")
                slots.add(value)
                zone, index = slot.groups()
                board = zone_boards.get(zone)
                value = self.board_uids[f"{board}{index}"] if board else ""
            elif value:
                if not re.fullmatch(r"[0-9a-f]{8}", value):
                    raise ValueError(f"Invalid NFC UID/reference on {tag['tid']}: {value}")
                if value in all_board_uids:
                    raise ValueError(f"Board UID also assigned to fixed tag {tag['tid']}")
            tag["nfc_uid"] = value
            if value:
                if value in by_uid:
                    raise ValueError(f"Duplicate mapped NFC UID: {value}")
                by_uid[value] = number
            tags[number] = tag
        self.tags.clear()
        self.tags.update(tags)
        self.by_uid = by_uid
        self.ignored_uids = all_board_uids - set(by_uid)
        self.board_zones = frozenset(zone.upper() for zone in zone_boards)

    def lookup(self, uid):
        uid = uid.lower()
        if not uid:
            return TagMatch("no_read")
        if uid in self.by_uid:
            return TagMatch("mapped", self.by_uid[uid])
        return TagMatch("ignored" if uid in self.ignored_uids else "unknown")
