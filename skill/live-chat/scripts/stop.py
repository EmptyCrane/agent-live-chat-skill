#!/usr/bin/env python3
"""Backward-compatible wrapper for `live_chat.py stop`."""

import sys

from live_chat_core.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["stop"] + sys.argv[1:]))
