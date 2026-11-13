"""Label four monitors and save their text serials in car order."""

import argparse
import configparser
from datetime import datetime
import os
from pathlib import Path
import re
import tempfile

from baseline import ROOT, collect
import pygame
from pygame._sdl2.video import Window, Renderer, Texture


def identities(report):
    rows = report["displays"]
    if len(rows) != 4:
        raise ValueError(f"Expected exactly four displays; found {len(rows)}.")
    serials = []
    for row in rows:
        active = [m for m in row["monitors"] if m["flags"] & 1]
        if len(active) != 1:
            raise ValueError(f"SDL {row['sdl_index']}: expected one active physical monitor.")
        edid = active[0]["edid"]
        serial = edid.get("text_serial", "").strip()
        if not serial or not edid.get("base_checksum_valid") or "\ufffd" in serial:
            raise ValueError(f"SDL {row['sdl_index']}: missing or invalid text serial/EDID.")
        serials.append(serial)
    if len({s.casefold() for s in serials}) != 4:
        raise ValueError("Monitor text serials are not unique.")
    return serials


def updated_config(original, serials):
    """Preserve comments and unrelated values; remove obsolete display indexes."""
    parser = configparser.ConfigParser()
    parser.read_string(original)
    result = original
    for car, serial in enumerate(serials, 1):
        section = f"car_{car}_display"
        if not parser.has_section(section):
            raise ValueError(f"Missing [{section}] section")
        pattern = rf"(?ms)(^\[{section}\][^\n]*\n)(.*?)(?=^\[|\Z)"
        def replace(match):
            body = re.sub(r"(?m)^display_index\s*=.*\n?", "", match[2])
            key = r"(?m)^monitor_text_serial\s*=.*$"
            line = f"monitor_text_serial = {serial}"
            if re.search(key, body):
                body = re.sub(key, lambda _: line, body)
            else:
                body = line + "\n" + body
            return match[1] + body
        result, count = re.subn(pattern, replace, result)
        if count != 1:
            raise ValueError(f"Cannot update [{section}]")
    check = configparser.ConfigParser()
    check.read_string(result)
    for car, serial in enumerate(serials, 1):
        if check.get(f"car_{car}_display", "monitor_text_serial") != serial:
            raise ValueError("Written serial did not round-trip")
    return result


def save(path, serials):
    raw = path.read_bytes()
    original = raw.decode("utf-8-sig")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    updated = updated_config(original.replace("\r\n", "\n"), serials)
    encoded = updated.replace("\n", newline).encode("utf-8")
    if raw.startswith(b"\xef\xbb\xbf"):
        encoded = b"\xef\xbb\xbf" + encoded
    backup_dir = ROOT / "logs/e14_monitor_identity"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"address_{datetime.now():%Y%m%d_%H%M%S_%f}.ini.bak"
    backup.write_bytes(raw)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        if path.read_bytes() != raw:
            raise RuntimeError("address.ini changed during calibration; run again.")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


def ask_order(report, serials):
    windows = []
    pygame.display.init()
    pygame.font.init()
    entered = ""
    message = ""
    try:
        for letter, row, serial in zip("ABCD", report["displays"], serials):
            position = 0x2FFF0000 | row["sdl_index"]
            window = Window(f"Monitor {letter} - calibration", size=tuple(row["desktop_size"]),
                            position=(position, position), fullscreen_desktop=True)
            windows.append((window, Renderer(window, accelerated=False), letter, serial))
        font = pygame.font.Font(None, 36)
        clock = pygame.time.Clock()
        dirty = True
        while True:
            for event in pygame.event.get():
                if event.type in (pygame.QUIT, pygame.WINDOWCLOSE):
                    return None
                if event.type != pygame.KEYDOWN:
                    continue
                dirty = True
                if event.key == pygame.K_ESCAPE:
                    return None
                if event.key == pygame.K_BACKSPACE:
                    entered = entered[:-1]
                    message = ""
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if len(entered) == 4 and set(entered) == set("ABCD"):
                        return entered
                    message = "Enter each of A, B, C, D exactly once."
                elif event.unicode.upper() in tuple("ABCD"):
                    letter = event.unicode.upper()
                    if letter in entered:
                        message = "That letter is already assigned. Backspace to edit."
                    elif len(entered) < 4:
                        entered += letter
                        message = ""
            if dirty:
                for window, renderer, letter, serial in windows:
                    width, height = window.size
                    surface = pygame.Surface((width, height))
                    surface.fill((20, 30, 48))
                    big = pygame.font.Font(None, min(height // 2, 500))
                    glyph = big.render(letter, True, (245, 210, 90))
                    surface.blit(glyph, glyph.get_rect(center=(width // 2, height // 3)))
                    lines = [f"Monitor serial: {serial}",
                             "Type the screen letters for CAR 1, CAR 2, CAR 3, CAR 4, in that order.",
                             "Example: CADB means car 1 = C, car 2 = A, car 3 = D, car 4 = B.",
                             "   ".join(f"Car {i + 1}: {entered[i] if i < len(entered) else '_'}" for i in range(4)),
                             "Enter = save serials     Backspace = edit     Escape = cancel", message]
                    for offset, line in enumerate(lines):
                        text = font.render(line, True, (235, 240, 250))
                        surface.blit(text, text.get_rect(center=(width // 2, height * 2 // 3 + offset * 42)))
                    renderer.clear()
                    texture = Texture.from_surface(renderer, surface)
                    texture.draw()
                    renderer.present()
                dirty = False
            clock.tick(30)
    finally:
        for window, *_ in windows:
            window.destroy()
        pygame.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate monitor identities without opening windows or writing config")
    args = parser.parse_args()
    report = collect()
    serials = identities(report)
    for letter, row, serial in zip("ABCD", report["displays"], serials):
        print(f"{letter}: SDL {row['sdl_index']}, serial {serial}, bounds {row['bounds']}")
    if args.check:
        print("Four unique monitor text serials available. No configuration written.")
        return
    order = ask_order(report, serials)
    if order is None:
        print("Cancelled. No configuration written.")
        return
    fresh = collect()
    if identities(fresh) != serials or [r["bounds"] for r in fresh["displays"]] != [r["bounds"] for r in report["displays"]]:
        raise RuntimeError("Display mapping changed during calibration; run again.")
    selected = [serials["ABCD".index(letter)] for letter in order]
    backup = save(ROOT / "config/address.ini", selected)
    for car, serial in enumerate(selected, 1):
        print(f"Car {car}: monitor_text_serial = {serial}")
    print(f"Saved serials to config/address.ini. Backup: {backup}")
    print("Controller startup now selects monitors by these text serials.")


if __name__ == "__main__":
    main()
