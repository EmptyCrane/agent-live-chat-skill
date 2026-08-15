"""Crash-safe JSON persistence helpers."""

import json
import os
import uuid
from pathlib import Path


def _atomic_write(path, write):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def atomic_json(path, value):
    """Atomically replace one compact UTF-8 JSON document."""

    def write(handle):
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")

    _atomic_write(path, write)


def atomic_jsonl(path, values):
    """Atomically replace one compact UTF-8 JSON Lines document."""

    def write(handle):
        for value in values:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")

    _atomic_write(path, write)
