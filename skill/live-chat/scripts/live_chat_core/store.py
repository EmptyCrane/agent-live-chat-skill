"""Thread-safe state storage with atomic snapshots and legacy migration."""

import json
import logging
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .models import DEFAULT_SESSION, initial_state
from .validation import (
    ValidationError,
    validate_message,
    validate_participants,
    validate_persisted_state,
    validate_scene,
    validate_session,
    validate_seed,
    validate_typing,
)


def _append_participant(state, name):
    if name and name not in state["participants"]:
        state["participants"].append(name)


class StateStore:
    def __init__(self, path, legacy_path=None, logger=None):
        self.path = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _load(self):
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    return validate_persisted_state(json.load(handle))
            except (OSError, ValueError, ValidationError) as exc:
                raise RuntimeError("cannot load state snapshot: %s" % exc) from exc
        if self.legacy_path and self.legacy_path.is_file():
            state, accepted, skipped = self._replay_legacy(self.legacy_path)
            self._persist(state)
            self.logger.info(
                "Migrated legacy history: accepted=%d skipped=%d source=%s",
                accepted,
                skipped,
                self.legacy_path,
            )
            return state
        state = initial_state()
        self._persist(state)
        return state

    def _persist(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(".%s.%s.tmp" % (self.path.name, uuid.uuid4().hex))
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self.path))
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass

    def _replay_legacy(self, path):
        state = initial_state()
        accepted = 0
        skipped = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_type = event.get("type")
                    if event_type == "msg":
                        message = validate_message(event.get("msg", {}), len(state["messages"]) + 1)
                        state["messages"].append(message)
                        if not message["sys"]:
                            _append_participant(state, message["sender"])
                    elif event_type == "typing":
                        raw = event.get("typing", {})
                        if not isinstance(raw, dict):
                            raise ValidationError("invalid_typing", "legacy typing must be an object")
                        state["typing"] = {
                            str(sender).strip(): True
                            for sender, active in raw.items()
                            if active is True and str(sender).strip()
                        }
                        for sender in state["typing"]:
                            _append_participant(state, sender)
                    elif event_type == "scene":
                        state["scene"] = validate_scene(event.get("scene", {}))
                    elif event_type == "reset":
                        state["messages"] = []
                        state["typing"] = {}
                        state["participants"] = []
                        state["session"] = deepcopy(DEFAULT_SESSION)
                        state["epoch"] = max(state["epoch"] + 1, int(event.get("epoch", 0)))
                        if event.get("scene"):
                            state["scene"] = validate_scene(event["scene"])
                    elif event_type == "seed":
                        normalized = validate_seed(event)
                        state["messages"] = normalized["messages"]
                        state["typing"] = {}
                        state["participants"] = normalized["participants"] or []
                        if normalized["participants"] is None:
                            for message in state["messages"]:
                                if not message["sys"]:
                                    _append_participant(state, message["sender"])
                        if normalized["session"] is not None:
                            state["session"] = normalized["session"]
                            for role in state["session"]["roles"]:
                                _append_participant(state, role["name"])
                        else:
                            state["session"] = deepcopy(DEFAULT_SESSION)
                        state["epoch"] = max(state["epoch"] + 1, int(event.get("epoch", 0)))
                        if normalized["scene"]:
                            state["scene"] = normalized["scene"]
                    else:
                        raise ValidationError("unknown_event", "unknown legacy event")
                    accepted += 1
                    state["revision"] += 1
                except (ValueError, TypeError, ValidationError):
                    skipped += 1
        state["messages"] = [
            dict(message, id=index + 1) for index, message in enumerate(state["messages"])
        ]
        return validate_persisted_state(state), accepted, skipped

    def _commit(self, mutate):
        with self._lock:
            candidate = deepcopy(self._state)
            result = mutate(candidate)
            candidate["revision"] += 1
            candidate = validate_persisted_state(candidate)
            self._persist(candidate)
            self._state = candidate
            return deepcopy(result), candidate["epoch"], candidate["revision"]

    def snapshot(self, since=0):
        with self._lock:
            total = len(self._state["messages"])
            return {
                "epoch": self._state["epoch"],
                "revision": self._state["revision"],
                "event_seq": self._state.get("event_seq", 0),
                "total": total,
                "scene": deepcopy(self._state["scene"]),
                "session": deepcopy(self._state["session"]),
                "participants": deepcopy(self._state["participants"]),
                "typing": deepcopy(self._state["typing"]),
                "messages": deepcopy(self._state["messages"][since:]),
            }

    def add_message(self, payload):
        def mutate(state):
            message = validate_message(payload, len(state["messages"]) + 1)
            if not message["ts"]:
                message["ts"] = datetime.now().strftime("%H:%M:%S")
            state["messages"].append(message)
            if not message["sys"]:
                _append_participant(state, message["sender"])
            return message

        message, epoch, revision = self._commit(mutate)
        return {"ok": True, "id": message["id"], "epoch": epoch, "revision": revision}

    def set_typing(self, payload):
        normalized = validate_typing(payload)

        def mutate(state):
            if normalized["clear"]:
                state["typing"] = {}
            elif normalized["active"]:
                state["typing"][normalized["sender"]] = True
                _append_participant(state, normalized["sender"])
            else:
                state["typing"].pop(normalized["sender"], None)
            return deepcopy(state["typing"])

        typing, epoch, revision = self._commit(mutate)
        return {"ok": True, "typing": typing, "epoch": epoch, "revision": revision}

    def set_participants(self, participants):
        normalized = validate_participants(participants)

        def mutate(state):
            state["participants"] = normalized
            return normalized

        result, epoch, revision = self._commit(mutate)
        return {
            "ok": True,
            "participants": result,
            "epoch": epoch,
            "revision": revision,
        }

    def set_session(self, session):
        def mutate(state):
            normalized = (
                deepcopy(DEFAULT_SESSION)
                if session is None
                else validate_session(session, state["participants"])
            )
            state["session"] = normalized
            return normalized

        result, epoch, revision = self._commit(mutate)
        return {"ok": True, "session": result, "epoch": epoch, "revision": revision}

    def set_scene(self, scene):
        normalized = validate_scene(scene)

        def mutate(state):
            state["scene"] = normalized
            return normalized

        result, epoch, revision = self._commit(mutate)
        return {"ok": True, "scene": result, "epoch": epoch, "revision": revision}

    def reset(self, scene=None):
        normalized = validate_scene(scene) if scene is not None else None

        def mutate(state):
            state["messages"] = []
            state["typing"] = {}
            state["session"] = deepcopy(DEFAULT_SESSION)
            state["epoch"] += 1
            if normalized is not None:
                state["scene"] = normalized
            return None

        _, epoch, revision = self._commit(mutate)
        return {"ok": True, "count": 0, "epoch": epoch, "revision": revision}

    def seed(self, payload):
        normalized = validate_seed(payload)

        def mutate(state):
            state["messages"] = normalized["messages"]
            state["typing"] = {}
            if normalized["participants"] is not None:
                state["participants"] = normalized["participants"]
            else:
                state["participants"] = []
                for message in state["messages"]:
                    if not message["sys"]:
                        _append_participant(state, message["sender"])
            if normalized["session"] is not None:
                state["session"] = normalized["session"]
                for role in state["session"]["roles"]:
                    _append_participant(state, role["name"])
            else:
                state["session"] = deepcopy(DEFAULT_SESSION)
            state["epoch"] += 1
            if normalized["scene"] is not None:
                state["scene"] = normalized["scene"]
            return len(state["messages"])

        count, epoch, revision = self._commit(mutate)
        return {"ok": True, "count": count, "epoch": epoch, "revision": revision}
