"""Send one tool command through the robot; motor stopping is manual."""


import socket
from time import sleep

with socket.create_connection(("192.168.0.10", 54321), timeout=3) as connection:
    connection.sendall(b"PING\n")
    connection.sendall(b"TIGHTEN M1\n")
    sleep(2)
    connection.sendall(b"STOP\n")