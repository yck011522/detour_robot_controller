"""Read-only SDL/Windows monitor inventory. No cameras or robot connections."""
import configparser
from datetime import datetime
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from game_controller.monitors import collect

if __name__ == "__main__":
    report = collect()
    config = configparser.ConfigParser()
    config.read(ROOT / "config/address.ini")
    for row in report["displays"]:
        row["configured_cars"] = []
        for car in range(1, 5):
            serial = config.get(f"car_{car}_display", "monitor_text_serial", fallback="")
            if any(m["edid"].get("text_serial") == serial for m in row["monitors"]):
                row["configured_cars"].append(car)
    rendered = json.dumps(report, indent=2)
    print(rendered)
    print("\nSDL  Car(s)  Windows device  Position       Model / text serial")
    for row in report["displays"]:
        cars = ",".join(map(str, row["configured_cars"])) or "-"
        identities = "; ".join(
            f"{item['edid'].get('model', item['name'])} / {item['edid'].get('text_serial', 'unavailable')}"
            for item in row["monitors"]
        ) or "No monitor identity returned"
        print(f"{row['sdl_index']:<5}{cars:<8}{row['windows_device']:<16}"
              f"{str(tuple(row['bounds'][:2])):<15}{identities}")
    directory = ROOT / "logs/e14_monitor_identity"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"baseline_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    destination.write_text(rendered + "\n", encoding="utf-8")
    print(f"\nSaved: {destination}")
