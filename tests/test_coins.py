"""Synthetic NFC visits and real INI round trips; no hardware or live motion."""

from pathlib import Path
import tempfile
import unittest

from game_controller.coins import CoinGame, CoinStore


def tags():
    return {
        1: {"tid": "T1", "track": "1", "nfc_uid": "11111111", "video_id": ""},
        2: {"tid": "T2", "track": "1", "nfc_uid": "22222222", "video_id": "2"},
        3: {"tid": "T3", "track": "2", "nfc_uid": "33333333", "video_id": ""},
        4: {"tid": "T4", "track": "", "nfc_uid": "44444444", "video_id": ""},
    }


class CoinTests(unittest.TestCase):
    def test_values_eligibility_and_independent_totals(self):
        game = CoinGame(tags())
        self.assertEqual(game.observe(1, "11111111", 0)["points"], 1)
        self.assertEqual(game.observe(1, "22222222", 1)["points"], 10)
        self.assertIsNone(game.observe(1, "33333333", 2))
        self.assertIsNone(game.observe(1, "44444444", 3))
        self.assertEqual(game.observe(2, "33333333", 4)["total"], 1)
        self.assertEqual(game.totals, [11, 1, 0, 0])

    def test_respawn_requires_departure_and_return_even_after_reconnect(self):
        game = CoinGame(tags())
        game.observe(1, "11111111", 0)
        self.assertEqual(game.hidden(1, 119.999), (1,))
        self.assertEqual(game.hidden(1, 120), ())
        for now in (1, 119, 120, 1000):
            self.assertIsNone(game.observe(1, "11111111", now))
        # Telemetry loss/no-read must not count as leaving the physical tag.
        self.assertIsNone(game.observe(1, "", 1001))
        self.assertIsNone(game.observe(1, "11111111", 1002))
        game.observe(1, "unknown!", 1003)
        self.assertEqual(game.observe(1, "11111111", 1004)["total"], 2)

    def test_early_return_does_not_collect_when_timer_expires_under_car(self):
        game = CoinGame(tags(), silver_points=3, gold_points=17, respawn_seconds=5)
        self.assertEqual(game.observe(1, "22222222", 0)["points"], 17)
        game.observe(1, "11111111", 1)
        self.assertIsNone(game.observe(1, "22222222", 4))
        self.assertIsNone(game.observe(1, "22222222", 5))
        game.observe(1, "unknown!", 6)
        self.assertEqual(game.observe(1, "22222222", 7)["total"], 37)

    def test_persistence_coalesces_flushes_and_restart_resets_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coin_count.ini"
            store = CoinStore(path)
            game = CoinGame(tags(), store.totals)
            for count in range(100):
                store.submit((count, 20, 30, 40))
            game.observe(1, "11111111", 0)
            store.submit(game.totals)
            store.close()
            restarted = CoinStore(path)
            try:
                fresh = CoinGame(tags(), restarted.totals)
                self.assertEqual(fresh.totals, [1, 0, 0, 0])
                self.assertEqual(fresh.hidden(1, 0), ())
                self.assertEqual(fresh.observe(1, "11111111", 0)["total"], 2)
            finally:
                restarted.close()

    def test_invalid_saved_totals_are_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coin_count.ini"
            path.write_text("[coins]\ncar_1 = broken\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                CoinStore(path)

    def test_persistence_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CoinStore(Path(directory) / "missing" / "coin_count.ini")
            with self.assertRaisesRegex(RuntimeError, "Could not save coin totals"):
                store.close()

    def test_invalid_parameters_and_duplicate_uids(self):
        for options in ({"respawn_seconds": 0}, {"respawn_seconds": float("nan")},
                        {"gold_points": -1}, {"silver_points": 1.5}):
            with self.assertRaises(ValueError):
                CoinGame(tags(), **options)
        duplicate = tags()
        duplicate[2]["nfc_uid"] = duplicate[1]["nfc_uid"]
        with self.assertRaisesRegex(ValueError, "Duplicate NFC UID"):
            CoinGame(duplicate)
