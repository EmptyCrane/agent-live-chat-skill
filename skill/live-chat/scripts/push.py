#!/usr/bin/env python3
"""Backward-compatible wrapper for live-chat push commands."""

import sys

from live_chat_core.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
