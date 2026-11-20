"""Read-only tool connection check: PING/STATUS, one retry on explicit rejection."""

from datetime import datetime
import json
import time

from game_controller.robot import tool
from game_controller.robot.settings import ROOT


class ProbeLog:
    def __init__(self, file):
        self.file = file

    def write(self, event, **data):
        record = dict(time=datetime.now().astimezone().isoformat(), event=event, **data)
        line = json.dumps(record)
        self.file.write(line + "\n")
        self.file.flush()
        print(line, flush=True)


def main():
    directory = ROOT / "logs/tool_probe"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl"
    print(f"Log: {path}", flush=True)
    passed = 0
    with path.open("x", encoding="utf-8") as file:
        log = ProbeLog(file)
        for index in range(3):
            client = None
            log.write("CONNECTION_START", index=index + 1)
            try:
                client = tool.LineClient(tool.host_from_config(), tool.tool_port(), log)
                for command, expected in (("PING", "OK PONG"), ("STATUS", None), ("PING", "OK PONG")):
                    reply = client.command(command, expected)
                    if command == "STATUS":
                        words = reply.split()
                        if len(words) != 5 or words[0] != "OK" or any(
                                state not in {"IDLE", "TIGHTENING", "LOOSENING", "STALLED"}
                                for state in words[1:3]):
                            raise RuntimeError(f"Malformed STATUS: {reply}")
                        current, pwm = map(int, words[3:])
                        if not -1500 <= current <= 1500 or not 0 <= pwm <= 100:
                            raise RuntimeError(f"Invalid STATUS values: {reply}")
                passed += 1
                log.write("CONNECTION_PASS", index=index + 1)
            except Exception as error:
                log.write("CONNECTION_FAILED", index=index + 1, reason=str(error))
            finally:
                if client is not None:
                    client.close()
            time.sleep(.2)
        log.write("RESULT", passed=passed, connections=3, commands="PING/STATUS only")
    return int(passed != 3)


if __name__ == "__main__":
    raise SystemExit(main())
