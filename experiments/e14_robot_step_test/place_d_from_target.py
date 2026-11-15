"""Compatibility entry point; preview by default, pass --live to execute."""

import sys
from zone_flow import main


if __name__ == "__main__":
    raise SystemExit(main(['place', 'd', '--from-target'] + sys.argv[1:]))
