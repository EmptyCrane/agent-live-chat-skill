"""Thread-safe state storage with atomic snapshots and legacy migration."""

import json
import logging
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .io_utils import atomic_json
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
        atomic_json(self.path, state)

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

    def _apply_operation(self, state, operation, payload):
        if operation == "message":
            message = validate_message(payload, len(state["messages"]) + 1)
            if not message["ts"]:
                message["ts"] = datetime.now().strftime("%H:%M:%S")
            state["messages"].append(message)
            if not message["sys"]:
                _append_participant(state, message["sender"])
            return message
        if operation == "typing":
            normalized = validate_typing(payload)
            if normalized["clear"]:
                state["typing"] = {}
            elif normalized["active"]:
                state["typing"][normalized["sender"]] = True
                _append_participant(state, normalized["sender"])
            else:
                state["typing"].pop(normalized["sender"], None)
            return deepcopy(state["typing"])
        if operation == "participants":
            normalized = validate_participants(payload)
            state["participants"] = normalized
            return normalized
        if operation == "session":
            normalized = (
                deepcopy(DEFAULT_SESSION)
                if payload is None
                else validate_session(payload, state["participants"])
            )
            state["session"] = normalized
            return normalized
        if operation == "scene":
            normalized = validate_scene(payload)
            state["scene"] = normalized
            return normalized
        if operation == "reset":
            normalized = validate_scene(payload) if payload is not None else None
            state["messages"] = []
            state["typing"] = {}
            state["session"] = deepcopy(DEFAULT_SESSION)
            state["epoch"] += 1
            if normalized is not None:
                state["scene"] = normalized
            return None
        if operation == "seed":
            normalized = validate_seed(payload)
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
        raise ValueError("unknown state operation: %s" % operation)

    @staticmethod
    def _operation_result(operation, result, epoch, revision):
        value = {"ok": True, "epoch": epoch, "revision": revision}
        if operation == "message":
            value["id"] = result["id"]
        elif operation == "typing":
            value["typing"] = result
        elif operation == "participants":
            value["participants"] = result
        elif operation == "session":
            value["session"] = result
        elif operation == "scene":
            value["scene"] = result
        elif operation in {"reset", "seed"}:
            value["count"] = 0 if operation == "reset" else result
        return value

    def apply_operations(self, operations):
        """Apply validated state operations atomically with one snapshot write."""
        if not operations:
            return []
        with self._lock:
            candidate = deepcopy(self._state)
            results = []
            last_operation_validated = False
            for operation, payload in operations:
                result = self._apply_operation(candidate, operation, payload)
                candidate["revision"] += 1
                # Participant/session replacement and whole-state operations can
                # violate cross-field invariants immediately. Message, typing,
                # and scene operations are already locally normalized, so defer
                # their full-state pass until the end of the transaction.
                last_operation_validated = operation in {
                    "participants",
                    "session",
                    "reset",
                    "seed",
                }
                if last_operation_validated:
                    candidate = validate_persisted_state(candidate)
                results.append(
                    self._operation_result(
                        operation,
                        deepcopy(result),
                        candidate["epoch"],
                        candidate["revision"],
                    )
                )
            if not last_operation_validated:
                candidate = validate_persisted_state(candidate)
            self._persist(candidate)
            self._state = candidate
            return deepcopy(results)

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

    def summary(self):
        """Return health metadata without copying conversation content."""
        with self._lock:
            return {
                "epoch": self._state["epoch"],
                "revision": self._state["revision"],
            }

    def add_message(self, payload):
        return self.apply_operations([("message", payload)])[0]

    def set_typing(self, payload):
        return self.apply_operations([("typing", payload)])[0]

    def set_participants(self, participants):
        return self.apply_operations([("participants", participants)])[0]

    def set_session(self, session):
        return self.apply_operations([("session", session)])[0]

    def set_scene(self, scene):
        return self.apply_operations([("scene", scene)])[0]

    def reset(self, scene=None):
        return self.apply_operations([("reset", scene)])[0]

    def seed(self, payload):
        return self.apply_operations([("seed", payload)])[0]
