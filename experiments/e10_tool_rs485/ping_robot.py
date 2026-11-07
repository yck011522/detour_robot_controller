"""Minimal read-only UR RS-485 check. Leave robot power and motors unchanged."""

import configparser
from pathlib import Path
import socket


config = configparser.ConfigParser(interpolation=None)
config.read(Path(__file__).resolve().parents[2] / "config" / "address.ini")
host = config["robotic_arm"]["host"]

# Plain TCP byte stream through the installed URCap; no custom client wrapper.
with socket.create_connection((host, 54321), timeout=3) as connection:
    connection.settimeout(3)
    connection.sendall(b"PING\n")
    response = bytearray()
    while b"\n" not in response:
        chunk = connection.recv(256)
        if not chunk:
            raise ConnectionError("Forwarder closed before a complete reply")
        response.extend(chunk)
        if len(response) > 4096:
            raise ValueError("Unexpected overlong reply")
    print(repr(bytes(response)), flush=True)
    if bytes(response).split(b"\n", 1)[0].rstrip(b"\r") != b"OK PONG":
        raise ValueError("Expected OK PONG")
