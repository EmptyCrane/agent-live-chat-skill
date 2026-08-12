#!/usr/bin/env python3
"""Backward-compatible wrapper for `live_chat.py start`."""

import sys

sys.dont_write_bytecode = True

from live_chat_core.cli import main


if __name__ == "__main__":
    arguments = list(sys.argv[1:])
    if arguments and arguments[0].isdigit():
        arguments = ["--port", arguments[0]] + arguments[1:]
    raise SystemExit(main(["start"] + arguments))
