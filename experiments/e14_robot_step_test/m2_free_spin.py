"""Supervised 15-second M2 tightening test; no robot motion/control interface."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e10_tool_rs485"))
import tool_test as protocol


def main():
    log = protocol.Log("m2_free_spin_15s")
    tool = None
    try:
        tool = protocol.LineClient(protocol.host_from_config(), 54321, log)
        tool.command("PING", "OK PONG")
        protocol.run_motor(tool, 2, "TIGHTEN", 15., log)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        log.write("TEST_INCOMPLETE", reason=str(error) or "Operator interruption")
        return 1
    finally:
        try:
            if tool:
                try:
                    protocol.stop_and_verify(tool)
                except Exception as error:
                    log.write("STOP_UNCONFIRMED", reason=str(error))
                    raise
                finally:
                    tool.close()
        finally:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
