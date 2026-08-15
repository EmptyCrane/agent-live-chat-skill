"""Crash-safe persistence and cross-process coordination helpers."""

import contextlib
import json
import os
import time
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


def _try_lock(handle):
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle):
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def exclusive_file_lock(path, timeout=10.0, interval=0.05):
    """Hold one cross-process advisory lock without storing runtime metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for startup lock") from exc
                time.sleep(interval)
        try:
            yield
        finally:
            try:
                _unlock(handle)
            except OSError:
                pass
    finally:
        handle.close()
