"""Bounded recovery of the observed first-STOP rejection through the forwarder."""

import socket
import unittest
from unittest.mock import MagicMock, call, patch

from game_controller.robot import tool


class ToolStopTests(unittest.TestCase):
    def client(self, replies):
        connection = MagicMock()
        connection.recv.side_effect = replies
        log = MagicMock()
        with patch.object(tool.socket, "create_connection", return_value=connection):
            client = tool.LineClient("robot", 54321, log)
        return client, connection, log

    def test_first_rejection_recovers_only_after_ack_and_idle_status(self):
        client, connection, log = self.client([
            b"ERR 1 unknown command\n", b"OK STOP\n", b"OK IDLE IDLE 3 0\n"])
        tool.stop_tool(client)
        self.assertEqual(connection.sendall.call_args_list,
                         [call(b"STOP\n"), call(b"STOP\n"), call(b"STATUS\n")])
        self.assertEqual(sum(c.args[0] == "TOOL_COMMAND_RETRY" for c in log.write.call_args_list), 1)

    def test_normal_stop_needs_no_retry(self):
        client, connection, _ = self.client([b"OK STOP\n", b"OK IDLE IDLE -1 0\n"])
        tool.stop_tool(client)
        self.assertEqual(connection.sendall.call_args_list, [call(b"STOP\n"), call(b"STATUS\n")])

    def test_only_first_request_gets_forwarder_startup_deadline(self):
        client, connection, _ = self.client([b"OK PONG\n"] * 2)
        with patch.object(tool.time, "monotonic", return_value=10.):
            client.command("PING", "OK PONG")
            self.assertEqual(connection.settimeout.call_args.args, (1.5,))
            client.command("PING", "OK PONG")
            self.assertAlmostEqual(connection.settimeout.call_args.args[0], .4)

    def test_explicit_timeout_is_preserved_on_first_request(self):
        client, connection, _ = self.client([b"OK PONG\n"])
        with patch.object(tool.time, "monotonic", return_value=10.):
            client.command("PING", "OK PONG", timeout=.2)
        self.assertAlmostEqual(connection.settimeout.call_args.args[0], .2)

    def test_second_rejection_is_not_retried_again(self):
        client, connection, _ = self.client([b"ERR 1 unknown command\n"] * 2)
        with self.assertRaises(tool.CommandRejected):
            tool.stop_tool(client)
        self.assertEqual(connection.sendall.call_count, 2)

    def test_transport_failure_or_other_rejection_is_not_retried(self):
        for reply in (socket.timeout("no reply"), b"", b"ERR 2 unavailable\n", b"unexpected\n"):
            with self.subTest(reply=reply):
                client, connection, _ = self.client([reply])
                with self.assertRaises((TimeoutError, ConnectionError, RuntimeError)):
                    tool.stop_tool(client)
                connection.sendall.assert_called_once_with(b"STOP\n")

    def test_recovery_still_rejects_active_or_malformed_status(self):
        for reply in (b"OK IDLE TIGHTENING 3 20\n", b"OK IDLE IDLE 3 20\n", b"garbage\n"):
            with self.subTest(reply=reply):
                client, _, _ = self.client([b"ERR 1 unknown command\n", b"OK STOP\n", reply])
                with self.assertRaises(RuntimeError):
                    tool.stop_tool(client)

    def test_commands_retry_once_only_on_explicit_unknown_rejection(self):
        for command in ("PING", "STATUS", "TIGHTEN M1", "LOOSEN M2"):
            with self.subTest(command=command):
                client, connection, _ = self.client([b"ERR 1 unknown command\n", b"OK accepted\n"])
                self.assertEqual(client.command(command, "OK accepted"), "OK accepted")
                self.assertEqual(connection.sendall.call_count, 2)
                client, connection, _ = self.client([b"ERR 1 unknown command\n"] * 2)
                with self.assertRaises(tool.CommandRejected):
                    client.command(command)
                self.assertEqual(connection.sendall.call_count, 2)

    def test_motor_command_timeout_never_retries(self):
        for command in ("TIGHTEN M1", "LOOSEN M2"):
            with self.subTest(command=command):
                client, connection, _ = self.client([socket.timeout("no reply")])
                with self.assertRaises(TimeoutError):
                    client.command(command)
                connection.sendall.assert_called_once()


if __name__ == "__main__":
    unittest.main()
