"""Regenerate production split HUD SVGs and PNGs. Run with Python on Windows."""

from pathlib import Path
import xml.etree.ElementTree as ET
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "asset" / "ui_layout"
TEMPLATES = HERE / "hud_sources"
NS = "{http://www.w3.org/2000/svg}"
ET.register_namespace("", NS[1:-1])

for name, original, directions in [
    ("split_left_static", "split_left", ["left", "middle"]),
    ("split_right_static", "split_right", ["middle", "right"]),
    ("split_leftright_static", "split_leftright", ["left", "right"]),
]:
    tree = ET.parse(TEMPLATES / (original + ".svg"))
    root = tree.getroot()
    root.set("width", "306")
    root.set("viewBox", "0 0 306 326")
    for el in list(root):
        if el.tag == NS + "rect" and el.get("y") == "0":
            el.set("width", "306")
        if el.tag == NS + "polyline" and el.get("stroke") in ("#ffffff", "#ff741f"):
            el.set("stroke", "#ff741f")
            el.set(
                "points",
                " ".join(
                    f"{float(x) + 20:.3f},{y}"
                    for x, y in (point.split(",") for point in el.get("points").split())
                ),
            )
        elif el.tag == NS + "polyline":
            el.set("points", "21,73 285,73")
        if (el.tag == NS + "rect" and el.get("y") == "247") or (
            el.tag == NS + "text" and float(el.get("y", 0)) >= 247
        ):
            root.remove(el)
    chinese_labels = {"left": "摇桿向左", "right": "摇桿向右", "middle": "摇桿回中"}
    for direction, x in zip(directions, [76.5, 229.5]):
        for label, y, size, family, color in [
            ("Hold", 240, 17, "Segoe UI", "#22262d"),
            (chinese_labels[direction], 265, 17, "Microsoft JhengHei", "#22262d"),
            (f"Joystick {direction.upper()}", 292, 15, "Segoe UI", "#22262d"),
        ]:
            text = ET.SubElement(
                root,
                NS + "text",
                {
                    "x": str(x),
                    "y": str(y),
                    "font-size": str(size),
                    "text-anchor": "middle",
                    "fill": color,
                    "font-family": family,
                    "dominant-baseline": "text-before-edge",
                    "font-weight": "700",
                },
            )
            text.text = label
    tree.write(
        SOURCE / (name + ".svg"),
        encoding="utf-8",
        xml_declaration=True,
    )

subprocess.run(
    ["powershell.exe", "-NoProfile", "-File", str(HERE / "render_split_hud.ps1")],
    check=True,
)
