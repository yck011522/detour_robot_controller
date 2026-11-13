import unittest

from game_controller.monitors import resolve_serials


def display(index, serial):
    return {"sdl_index": index, "monitors": [{"flags": 1, "edid": {
        "text_serial": serial, "base_checksum_valid": True}}]}


class MonitorTests(unittest.TestCase):
    def test_serials_follow_monitors_when_indexes_change(self):
        serials = ["first", "second"]
        self.assertEqual(resolve_serials(serials, {"displays": [display(0, "first"), display(1, "second")]}), [0, 1])
        self.assertEqual(resolve_serials(serials, {"displays": [display(0, "second"), display(1, "first")]}), [1, 0])

    def test_missing_and_ambiguous_monitors_fail(self):
        for rows in ([], [display(0, "first"), display(1, "first")]):
            with self.assertRaises(RuntimeError):
                resolve_serials(["first"], {"displays": rows})

    def test_invalid_edid_is_not_used(self):
        row = display(0, "first")
        row["monitors"][0]["edid"]["base_checksum_valid"] = False
        with self.assertRaises(RuntimeError):
            resolve_serials(["first"], {"displays": [row]})

    def test_duplicate_config_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_serials(["first", " FIRST "], {"displays": []})
