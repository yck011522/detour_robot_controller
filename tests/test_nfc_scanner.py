"""Wired scanner protocol, serial framing and named UID persistence checks."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from utility.NFC_tag_scanner import Scanner, Scan, load_tags, parse_packet, save_tag, scan_tags


class ScannerTests(unittest.TestCase):
    def test_protocol_matches_wired_mapper(self):
        self.assertEqual(parse_packet(b"NFC=60:d7:bc:e5,AGE_MS=42,READER=OK\r\n"),
                         ("60D7BCE5", 42, "OK"))
        self.assertEqual(parse_packet(b"NFC=NONE,AGE_MS=NA,READER=RECOVERING\n"),
                         ("", None, "RECOVERING"))
        for raw in (b"booting\n", b"NFC=123,AGE_MS=0,READER=OK\n",
                    b"NFC=12345678,AGE_MS=0\n", b"\xff\n"):
            self.assertIsNone(parse_packet(raw))

    def test_partial_serial_lines_and_no_read_preserve_last_scan(self):
        connection = Mock()
        connection.readline.side_effect = [
            b"NFC=60D7", b"BCE5,AGE_MS=25,READER=OK\n",
            b"NFC=NONE,AGE_MS=NA,READER=OK\n", OSError("unplugged")]
        scanner = Scanner(connection)
        with patch("utility.NFC_tag_scanner.time.monotonic", return_value=10):
            scanner.run()
        scan = scanner.snapshot()
        self.assertEqual(scan.uid, "60D7BCE5")
        self.assertEqual(scan.age(10.5), 525)
        self.assertEqual(scan.error, "unplugged")

    def test_overlong_serial_message_is_not_parsed_as_a_valid_suffix(self):
        connection = Mock()
        connection.readline.side_effect = [
            b"x" * 4096, b"x", b"NFC=11111111,AGE_MS=0,READER=OK\n",
            b"NFC=22222222,AGE_MS=2,READER=OK\n", OSError("done")]
        scanner = Scanner(connection)
        scanner.run()
        self.assertEqual(scanner.snapshot().uid, "22222222")

    def test_multiple_registrations_reload_and_duplicate_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic_nfc.ini"
            save_tag(path, "P1", "aa:bb:cc:dd")
            save_tag(path, "p2", "11223344")
            self.assertEqual(dict(load_tags(path)["tags"]),
                             {"p1": "AABBCCDD", "p2": "11223344"})
            before = path.read_bytes()
            for name, uid in (("q1", "AABBCCDD"), ("p1", "55667788"),
                              ("[invalid]", "01020304"), ("q2", "not-uid")):
                with self.assertRaises(ValueError):
                    save_tag(path, name, uid)
                self.assertEqual(path.read_bytes(), before)
            save_tag(path, "p1", "55667788", overwrite=True)
            self.assertEqual(load_tags(path)["tags"]["p1"], "55667788")

    def test_failed_atomic_replace_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic_nfc.ini"
            save_tag(path, "p1", "AABBCCDD")
            before = path.read_bytes()
            with patch("utility.NFC_tag_scanner.os.replace", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    save_tag(path, "p2", "11223344")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_save_freezes_uid_while_new_scans_arrive_during_naming(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic_nfc.ini"
            scanner = Mock()
            scanner.snapshot.return_value = Scan("AABBCCDD", 0, 0, "OK")

            def name_tag(prompt):
                scanner.snapshot.return_value = Scan("11223344", 0, 0, "OK")
                return "p1"

            with patch("utility.NFC_tag_scanner.read_key", side_effect=["s", "q"]), \
                    patch("builtins.input", side_effect=name_tag), \
                    patch("utility.NFC_tag_scanner.time.sleep"), redirect_stdout(io.StringIO()):
                scan_tags(scanner, path)
            self.assertEqual(load_tags(path)["tags"]["p1"], "AABBCCDD")

    def test_malformed_saved_uid_is_reported_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic_nfc.ini"
            path.write_text("[tags]\np1 = invalid\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid saved UID"):
                load_tags(path)
