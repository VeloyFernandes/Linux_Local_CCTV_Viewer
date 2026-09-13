#!/usr/bin/env python3
"""Convenience launcher for the ONVIF CCTV Monitor.

Usage:
    python run.py            # normal start, cameras restored from settings
    python run.py --demo     # start with synthetic test cameras

For a desktop launch without a terminal, use ``./run.sh`` instead
(see README.md).
"""
from cctv.app import main

if __name__ == "__main__":
    raise SystemExit(main())
