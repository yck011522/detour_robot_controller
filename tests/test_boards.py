"""Board startup/persistence and physical UID resolution, without hardware."""

import configparser
from dataclasses import replace
from contextlib import ExitStack, redirect_stdout
import io
from itertools import permutations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from game_controller.app import observe_location
from game_controller.boards import (BOARDS, BoardStartup, TagResolver,
                                    load_board_state, save_board_state, validate_arrangement)
from game_controller.coins import CoinGame
from game_controller.config import ROOT
from game_controller.minimap import load_tag_attributes, ASSET_DIRECTORY
from game_controller.protocol import Telemetry
from game_controller.state import Car


LAYOUT = {"P": "C", "Q": "A", "R": "B", "S": "spare"}


class BoardTests(unittest.TestCase):
    def test_main_suppresses_driving_until_layout_is_saved_and_confirmed(self):
        from game_controller import app
        config = app.load()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            config["log_directory"] = Path(directory)
            config["settings"]["shutdown_stop_s"] = .001
            startup = BoardStartup(Path(directory) / "board_state.ini")
            calls = []
            frames = 0

            def events():
                nonlocal frames
                frames += 1
                if frames == 1:
                    return [Mock(type=app.pygame.KEYDOWN, key=app.pygame.K_SPACE, repeat=False)]
                if frames == 2:
                    startup.draft = dict(LAYOUT)
                    return [Mock(type=app.pygame.KEYDOWN, key=app.pygame.K_RETURN, repeat=False)]
                return []

            def command(car, *args):
                calls.append((args[6], args[-1]))  # admin, stop-only gate
                if startup.confirmed:
                    self.assertEqual(car.navigation.board_zones, frozenset(("A", "B", "C")))
                return 1

            stack.enter_context(patch.object(app, "load", return_value=config))
            stack.enter_context(patch.object(app, "BoardStartup", return_value=startup))
            store_class = app.CoinStore
            stack.enter_context(patch.object(app, "CoinStore", side_effect=lambda path:
                                             store_class(Path(directory) / "coin_count.ini")))
            stack.enter_context(patch.object(app, "ask_boards_console"))
            stack.enter_context(patch("sys.argv", ["controller", "--no-video", "--seconds", ".14"]))
            for method in ("display.init", "font.init", "joystick.init", "display.set_mode", "quit"):
                stack.enter_context(patch("game_controller.app.pygame." + method))
            stack.enter_context(patch.object(app.pygame.event, "get", side_effect=events))
            stack.enter_context(patch.object(app.pygame.key, "get_focused", return_value=False))
            joystick = Mock()
            joystick.get_button.return_value = False
            joystick.get_name.return_value = "Synthetic gamepad"
            joystick.get_numbuttons.return_value = 12
            joystick.get_instance_id.return_value = 1
            stack.enter_context(patch.object(app, "connect_gamepad", return_value=joystick))
            stack.enter_context(patch.object(app, "open_modem", return_value=Mock()))
            modem = Mock()
            modem.drain.return_value = ([], [], {"session": 0, "connected": True, "invalid_lines": 0})
            stack.enter_context(patch.object(app, "Modem", return_value=modem))
            stack.enter_context(patch.object(app.Car, "command", new=command))
            with redirect_stdout(io.StringIO()):
                app.main()
            self.assertTrue(calls[0][1])
            self.assertTrue(any(not blocked for admin, blocked in calls))
            self.assertTrue(all(admin for admin, blocked in calls))  # Space was blocked at startup.
            self.assertEqual(load_board_state(startup.path), LAYOUT)

    def test_state_round_trip_and_prefill_require_confirmation_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board_state.ini"
            self.assertEqual(load_board_state(path)["S"], "spare")
            save_board_state(path, LAYOUT)
            self.assertEqual(load_board_state(path), LAYOUT)
            startup = BoardStartup(path)
            try:
                self.assertEqual(startup.draft, LAYOUT)
                self.assertFalse(startup.confirmed)
                startup.confirm()
                startup.future.result(timeout=2)
                self.assertFalse(startup.confirmed)  # Main tick commits after save.
                self.assertTrue(startup.poll())
                self.assertTrue(startup.confirmed)
                self.assertEqual(startup.saved, LAYOUT)
            finally:
                startup.close()
            recalled = BoardStartup(path)
            try:
                self.assertFalse(recalled.confirmed)
            finally:
                recalled.close()

    def test_invalid_assignments_and_form_editing(self):
        for state in ({}, {**LAYOUT, "P": "unassigned"}, {**LAYOUT, "P": "A"},
                      {**LAYOUT, "S": "D"}, {**LAYOUT, "S": "spare", "P": "spare"}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_arrangement(state)
        validate_arrangement({**LAYOUT, "P": "gripper"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board_state.ini"
            startup = BoardStartup(path)
            try:
                startup.confirm()
                self.assertIn("Assign every board", startup.error)
                self.assertFalse(path.exists())
                startup.cycle(1)
                self.assertEqual(startup.draft["P"], "A")
                startup.cycle(-1)
                self.assertEqual(startup.draft["P"], "unassigned")
            finally:
                startup.close()

    def test_failed_save_does_not_confirm_or_damage_previous_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board_state.ini"
            save_board_state(path, LAYOUT)
            before = path.read_bytes()
            startup = BoardStartup(path)
            try:
                with patch("game_controller.boards.os.replace", side_effect=OSError("disk error")):
                    startup.confirm()
                    with self.assertRaises(OSError):
                        startup.future.result(timeout=2)
                    self.assertFalse(startup.poll())
                self.assertFalse(startup.confirmed)
                self.assertIn("disk error", startup.error)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            finally:
                startup.close()


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.template = load_tag_attributes(ASSET_DIRECTORY)
        self.resolver = TagResolver(self.template, ROOT / "config/dynamic_nfc.ini")

    def test_all_240_valid_arrangements_resolve_slots_and_ignore_other_board_tags(self):
        arrangements = 0
        for locations in permutations(("A", "B", "C", "D", "gripper", "spare"), 4):
            if "spare" not in locations:
                continue
            layout = dict(zip(BOARDS, locations))
            self.resolver.assign(layout)
            self.assertEqual(self.resolver.board_zones,
                             frozenset(location for location in locations if location in "ABCD"))
            mapped = set()
            for number, tag in self.template.items():
                reference = tag["nfc_uid"]
                if not reference.startswith("<"):
                    continue
                zone, index = reference[1].upper(), reference[2]
                board = next((name for name, location in layout.items() if location == zone), None)
                if board:
                    uid = self.resolver.board_uids[board.lower() + index]
                    match = self.resolver.lookup(uid)
                    self.assertEqual((match.kind, match.tid), ("mapped", number))
                    self.assertEqual(self.resolver.tags[number]["inzone"], zone)
                    mapped.add(uid)
                else:
                    self.assertEqual(self.resolver.tags[number]["nfc_uid"], "")
            self.assertEqual(self.resolver.ignored_uids,
                             set(self.resolver.board_uids.values()) - mapped)
            for uid in self.resolver.ignored_uids:
                self.assertEqual(self.resolver.lookup(uid).kind, "ignored")
            arrangements += 1
        self.assertEqual(arrangements, 240)

    def test_fixed_tags_unaffected_and_assignment_does_not_modify_source_csv(self):
        before = (ASSET_DIRECTORY / "map_attributes.csv").read_bytes()
        self.resolver.assign(LAYOUT)
        uid = self.template[1]["nfc_uid"]
        self.assertEqual(self.resolver.lookup(uid.upper()).tid, 1)
        self.assertEqual(self.resolver.lookup("ffffffff").kind, "unknown")
        self.assertEqual(self.resolver.lookup("").kind, "no_read")
        self.assertEqual(self.template[72]["nfc_uid"], "<c2>")
        self.assertEqual((ASSET_DIRECTORY / "map_attributes.csv").read_bytes(), before)

    def test_ignored_tag_preserves_navigation_marker_and_coin_visit(self):
        self.resolver.assign(LAYOUT)
        coins = CoinGame(self.resolver.tags, resolver=self.resolver)
        car, car_tags = Car(), [None] * 4
        packet = Telemetry(2, 1, 1, 0, b"\x00" * 4, 0, 24, 0, 0, 0, 0, 12000)

        def visit(name, now):
            nonlocal packet
            uid = self.resolver.board_uids.get(name, name)
            packet = replace(packet, sequence=packet.sequence + 1, uid=bytes.fromhex(uid))
            result = car.accept(packet, now, 1, {}, .5)
            self.assertNotIn(result, ("duplicate", "stale"))
            return observe_location(car, packet, now, self.resolver, coins, car_tags)

        match, award = visit("p2", 0)
        self.assertEqual((match.tid, award["points"], car.navigation.inzone), (72, 1, "C"))
        last_visit = coins.last_uid[1]
        for ignored in ("p1", "s1", "s2", "s3"):
            match, award = visit(ignored, .1)
            self.assertEqual(match.kind, "ignored")
            self.assertIsNone(award)
            self.assertEqual((car.navigation.inzone, car.navigation.tid, car_tags[1]), ("C", "T72", 72))
            self.assertEqual(coins.last_uid[1], last_visit)
        # Ignored tags do not create a new visit after coin respawn either.
        self.assertIsNone(visit("p2", 200)[1])
        self.assertEqual(visit("p3", 200.1)[1]["total"], 2)
        # A truly unknown observed tag still clears context normally.
        self.assertEqual(visit("ffffffff", 200.2)[0].kind, "unknown")
        self.assertEqual(car.navigation.inzone, "")

    def test_ignored_tag_does_not_unlock_approach_or_replace_its_read_age(self):
        self.resolver.assign(LAYOUT)
        car = Car(uid="fixed-approach")
        car.navigation.observe(car.uid, 0, 0, {"tid": "T42", "split": "right", "reservezone": "A"})
        car.navigation.steering(False, True, 0)
        car.uid = self.resolver.board_uids["p1"]
        packet = Telemetry(1, 1, 1, 0, bytes.fromhex(car.uid), 0, 24, 0, 0, 0, 0, 12000)
        coins = CoinGame(self.resolver.tags, resolver=self.resolver)
        observe_location(car, packet, .2, self.resolver, coins, [None] * 4)
        self.assertEqual(car.navigation.uid, "fixed-approach")
        self.assertEqual(car.navigation.reservezone, "A")
        self.assertTrue(car.navigation.locked)

    def test_remapping_changes_destination_attributes_and_coin_eligibility(self):
        self.resolver.assign(LAYOUT)
        coins = CoinGame(self.resolver.tags, resolver=self.resolver)
        uid = self.resolver.board_uids["p2"]
        self.assertEqual(self.resolver.lookup(uid).tid, 72)
        self.resolver.assign({"P": "B", "Q": "A", "R": "C", "S": "spare"})
        self.assertEqual(self.resolver.lookup(uid).tid, 68)
        self.assertEqual(coins.totals, [0, 0, 0, 0])  # Remap itself awards nothing.
        self.assertIsNone(coins.observe(2, uid, 0))
        self.assertEqual(coins.observe(1, uid, 0)["points"], 1)

    def test_duplicate_registration_fixed_conflict_and_bad_slot_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic_nfc.ini"
            parser = configparser.ConfigParser()
            parser["tags"] = self.resolver.board_uids
            parser["tags"]["p1"] = parser["tags"]["p2"]
            with path.open("w") as target:
                parser.write(target)
            with self.assertRaisesRegex(ValueError, "Duplicate board"):
                TagResolver(self.template, path)
        conflict = {key: dict(value) for key, value in self.template.items()}
        conflict[1]["nfc_uid"] = self.resolver.board_uids["p1"]
        with self.assertRaisesRegex(ValueError, "fixed tag"):
            TagResolver(conflict, ROOT / "config/dynamic_nfc.ini")
        conflict[1]["nfc_uid"] = "<bad>"
        with self.assertRaisesRegex(ValueError, "Invalid NFC"):
            TagResolver(conflict, ROOT / "config/dynamic_nfc.ini")
