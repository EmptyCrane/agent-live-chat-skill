#!/usr/bin/env python3
"""Unified live-chat command entrypoint."""

import sys

sys.dont_write_bytecode = True

from live_chat_core.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
