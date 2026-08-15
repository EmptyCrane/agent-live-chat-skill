"""Multi-session catalog and versioned event stream."""

import json
import re
import shutil
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .adapters import EVENT_HOSTS
from .config import (
    CATALOG_SCHEMA_VERSION,
    EVENT_PROTOCOL_VERSION,
    SCHEMA_VERSION,
    sessions_dir,
    sessions_path,
)
from .io_utils import atomic_json, atomic_jsonl
from .models import initial_state
from .store import StateStore
from .validation import (
    DECISION_ACTIONS,
    DECISION_ID,
    ValidationError,
    utf8_safe_text,
    validate_pending_decision,
    validate_persisted_state,
    validate_scene,
    validate_session,
)


MAX_EVENTS = 5000
EVENT_TYPES = frozenset({
    "conversation.created",
    "conversation.selected",
    "conversation.archived",
    "conversation.restored",
    "conversation.reset",
    "conversation.seeded",
    "scene.updated",
    "plan.updated",
    "participants.replaced",
    "message.created",
    "typing.changed",
    "typing.cleared",
    "decision.requested",
    "decision.resolved",
    "round.started",
    "round.completed",
    "participant.started",
    "participant.completed",
    "participant.failed",
    "run.completed",
})
SESSION_EVENT_TYPES = frozenset({
    "plan.updated",
    "decision.requested",
    "decision.resolved",
    "round.started",
    "round.completed",
    "participant.started",
    "participant.completed",
    "participant.failed",
    "run.completed",
})
SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value, field, maximum, allow_empty=False):
    if not isinstance(value, str):
        raise ValidationError("invalid_event", "%s must be a string" % field)
    value = utf8_safe_text(value).strip()
    if not value and not allow_empty:
        raise ValidationError("invalid_event", "%s cannot be empty" % field)
    if len(value) > maximum:
        raise ValidationError("invalid_event", "%s is too long" % field)
    return value


def _utf8_safe_json(value):
    if isinstance(value, str):
        return utf8_safe_text(value)
    if isinstance(value, list):
        return [_utf8_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {
            utf8_safe_text(key) if isinstance(key, str) else key: _utf8_safe_json(item)
            for key, item in value.items()
        }
    return value


def _normalize_source(value):
    if value is None:
        return {"host": "manual"}
    if not isinstance(value, dict):
        raise ValidationError("invalid_event", "event source must be an object")
    host = _bounded_text(value.get("host", "manual"), "source.host", 32)
    if host not in EVENT_HOSTS:
        raise ValidationError("invalid_event", "unknown event source host")
    result = {"host": host}
    for field, maximum in (("actor", 64), ("run_id", 200), ("replay_of", 64)):
        if field in value and value[field] not in (None, ""):
            result[field] = _bounded_text(value[field], "source.%s" % field, maximum)
    return result


def _normalize_event_input(value):
    if not isinstance(value, dict):
        raise ValidationError("invalid_event", "event must be an object")
    event_type = _bounded_text(value.get("type", ""), "type", 64)
    if event_type not in EVENT_TYPES:
        raise ValidationError("invalid_event", "unsupported event type: %s" % event_type)
    payload = value.get("payload", {})
    if not isinstance(payload, dict):
        raise ValidationError("invalid_event", "event payload must be an object")
    result = {
        "type": event_type,
        "source": _normalize_source(value.get("source")),
        "payload": _utf8_safe_json(payload),
    }
    if value.get("event_id"):
        result["event_id"] = _bounded_text(value["event_id"], "event_id", 64)
    if value.get("occurred_at"):
        result["occurred_at"] = _bounded_text(value["occurred_at"], "occurred_at", 64)
    return result


def validate_event_input(value):
    """Validate one client event without mutating state."""
    return _normalize_event_input(value)


class SessionStore:
    """Coordinate immutable session identity, snapshots, and event history."""

    def __init__(self, state_dir, legacy_path=None, logger=None):
        self.state_dir = Path(state_dir)
        self.catalog_path = sessions_path(self.state_dir)
        self.sessions_root = sessions_dir(self.state_dir)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self.logger = logger
        self._lock = threading.RLock()
        self._stores = {}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self._catalog = self._load_or_initialize()

    def _session_dir(self, session_id):
        return self.sessions_root / session_id

    def _state_path(self, session_id):
        return self._session_dir(session_id) / "state.json"

    def _events_path(self, session_id):
        return self._session_dir(session_id) / "events.jsonl"

    def _load_catalog(self):
        try:
            with self.catalog_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RuntimeError("cannot load session catalog: %s" % exc) from exc
        if not isinstance(value, dict) or value.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise RuntimeError("cannot load session catalog: unsupported schema")
        entries = value.get("sessions")
        active = value.get("active_session_id")
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("cannot load session catalog: sessions must be a non-empty list")
        ids = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("session_id"), str)
                or not SESSION_ID.fullmatch(entry["session_id"])
                or not isinstance(entry.get("archived"), bool)
            ):
                raise RuntimeError("cannot load session catalog: invalid entry")
            ids.append(entry["session_id"])
        if len(ids) != len(set(ids)) or active not in ids:
            raise RuntimeError("cannot load session catalog: invalid active session")
        return value

    def _initial_snapshot(self):
        legacy_snapshot = self.state_dir / "state.json"
        if legacy_snapshot.is_file():
            try:
                with legacy_snapshot.open("r", encoding="utf-8") as handle:
                    return validate_persisted_state(json.load(handle)), "legacy"
            except (OSError, ValueError, ValidationError) as exc:
                raise RuntimeError("cannot import legacy state snapshot: %s" % exc) from exc
        if self.legacy_path and self.legacy_path.is_file():
            with tempfile.TemporaryDirectory(dir=str(self.state_dir)) as temporary:
                store = StateStore(Path(temporary) / "state.json", self.legacy_path, self.logger)
                snapshot = store.snapshot(0)
                snapshot["schema_version"] = SCHEMA_VERSION
                return snapshot, "legacy"
        return initial_state(), "manual"

    def _load_or_initialize(self):
        if self.catalog_path.exists():
            return self._load_catalog()
        state, host = self._initial_snapshot()
        session_id = uuid.uuid4().hex
        now = _utc_now()
        state = validate_persisted_state(state)
        state["event_seq"] = 1
        atomic_json(self._state_path(session_id), state)
        seed = {
            "scene": deepcopy(state["scene"]),
            "session": deepcopy(state["session"]),
            "participants": deepcopy(state["participants"]),
            "messages": deepcopy(state["messages"]),
        }
        initial_event = {
            "event_version": EVENT_PROTOCOL_VERSION,
            "event_id": uuid.uuid4().hex,
            "session_id": session_id,
            "seq": 1,
            "type": "conversation.seeded" if state["messages"] else "conversation.created",
            "occurred_at": now,
            "source": {"host": host},
            "payload": seed if state["messages"] else {},
        }
        atomic_jsonl(self._events_path(session_id), [initial_event])
        entry = self._metadata(session_id, state, now, now)
        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "active_session_id": session_id,
            "sessions": [entry],
        }
        atomic_json(self.catalog_path, catalog)
        return catalog

    def _metadata(self, session_id, state, created_at, updated_at, archived=False):
        return {
            "session_id": session_id,
            "title": state["scene"]["title"],
            "created_at": created_at,
            "updated_at": updated_at,
            "archived": bool(archived),
            "messages": len(state["messages"]),
            "status": state["session"]["status"],
        }

    def _entry(self, session_id):
        for entry in self._catalog["sessions"]:
            if entry["session_id"] == session_id:
                return entry
        raise ValidationError("unknown_session", "session does not exist", 404)

    def _store(self, session_id=None, writable=False):
        session_id = session_id or self._catalog["active_session_id"]
        entry = self._entry(session_id)
        if writable and entry["archived"]:
            raise ValidationError("session_archived", "archived sessions are read-only", 409)
        if session_id not in self._stores:
            self._recover(session_id)
            self._stores[session_id] = StateStore(self._state_path(session_id), logger=self.logger)
        return session_id, self._stores[session_id]

    def _recover(self, session_id):
        """Replay events committed after the last atomic snapshot."""
        events = self._read_events(session_id)
        try:
            with self._state_path(session_id).open("r", encoding="utf-8") as handle:
                state = validate_persisted_state(json.load(handle))
        except (OSError, ValueError, ValidationError) as exc:
            raise RuntimeError("cannot recover session snapshot: %s" % exc) from exc
        event_seq = state.get("event_seq", 0)
        if event_seq > len(events):
            raise RuntimeError("session snapshot is ahead of event history")
        pending = [event for event in events if event.get("seq", 0) > event_seq]
        if not pending:
            return
        with tempfile.TemporaryDirectory(dir=str(self._session_dir(session_id))) as temporary:
            staged_path = Path(temporary) / "state.json"
            atomic_json(staged_path, state)
            staged = StateStore(staged_path, logger=self.logger)
            self._apply_events_to_store(staged, pending)
            with staged_path.open("r", encoding="utf-8") as handle:
                recovered = json.load(handle)
        recovered["event_seq"] = events[-1]["seq"] if events else 0
        atomic_json(self._state_path(session_id), recovered)

    def _persist_catalog(self):
        atomic_json(self.catalog_path, self._catalog)

    def _read_events(self, session_id):
        events = []
        if not self._events_path(session_id).exists():
            return events
        try:
            with self._events_path(session_id).open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        events.append(json.loads(line))
        except (OSError, ValueError) as exc:
            raise RuntimeError("cannot load event history: %s" % exc) from exc
        seen = set()
        for index, event in enumerate(events, 1):
            if (
                not isinstance(event, dict)
                or event.get("event_version") not in (1, EVENT_PROTOCOL_VERSION)
                or event.get("session_id") != session_id
                or event.get("seq") != index
                or not isinstance(event.get("event_id"), str)
                or not event["event_id"]
                or event["event_id"] in seen
                or not isinstance(event.get("occurred_at"), str)
            ):
                raise RuntimeError("cannot load event history: invalid event at sequence %d" % index)
            _normalize_event_input(event)
            seen.add(event["event_id"])
        return events

    def _operation_for_event(self, event_type, payload):
        if event_type in {
            "conversation.created",
            "conversation.selected",
            "conversation.archived",
            "conversation.restored",
        }:
            return None
        if event_type == "message.created":
            return "message", payload
        if event_type == "typing.changed":
            return "typing", payload
        if event_type == "typing.cleared":
            return "typing", {"clear": True}
        if event_type == "participants.replaced":
            return "participants", payload.get("participants")
        if event_type in SESSION_EVENT_TYPES:
            if "session" not in payload:
                raise ValidationError("invalid_session", "event payload must contain session")
            return "session", payload.get("session")
        if event_type == "scene.updated":
            return "scene", payload.get("scene")
        if event_type == "conversation.reset":
            return "reset", payload.get("scene")
        if event_type == "conversation.seeded":
            return "seed", payload
        raise ValidationError("invalid_event", "unsupported event type")

    def _apply_events_to_store(self, store, events):
        results = [None] * len(events)
        operations = []
        operation_indexes = []
        for index, event in enumerate(events):
            operation = self._operation_for_event(event["type"], event["payload"])
            if operation is None:
                results[index] = {"ok": True}
            else:
                operations.append(operation)
                operation_indexes.append(index)
        for index, result in zip(operation_indexes, store.apply_operations(operations)):
            results[index] = result
        return results

    def _emit(self, values, session_id=None):
        if not isinstance(values, list) or not values or len(values) > MAX_EVENTS:
            raise ValidationError("invalid_event_batch", "events must contain 1-%d items" % MAX_EVENTS)
        with self._lock:
            session_id, current_store = self._store(session_id, writable=True)
            existing = self._read_events(session_id)
            by_id = {event["event_id"]: event for event in existing}
            normalized = [_normalize_event_input(value) for value in values]
            canonical = []
            new_values = []
            apply_flags = []
            for item in normalized:
                event_id = item.get("event_id") or uuid.uuid4().hex
                previous = by_id.get(event_id)
                if previous:
                    comparable = (previous["type"], previous["source"], previous["payload"])
                    if comparable != (item["type"], item["source"], item["payload"]):
                        raise ValidationError("event_conflict", "event_id already has different content", 409)
                    canonical.append(previous)
                    apply_flags.append(False)
                    continue
                event = {
                    "event_version": EVENT_PROTOCOL_VERSION,
                    "event_id": event_id,
                    "session_id": session_id,
                    "seq": len(existing) + len(new_values) + 1,
                    "type": item["type"],
                    "occurred_at": item.get("occurred_at") or _utc_now(),
                    "source": item["source"],
                    "payload": item["payload"],
                }
                canonical.append(event)
                new_values.append(event)
                apply_flags.append(True)
                by_id[event_id] = event
            if not new_values:
                return canonical, [{"ok": True, "duplicate": True} for _ in canonical]

            with tempfile.TemporaryDirectory(dir=str(self._session_dir(session_id))) as temporary:
                staged_path = Path(temporary) / "state.json"
                shutil.copy2(self._state_path(session_id), staged_path)
                staged = StateStore(staged_path, logger=self.logger)
                applied = iter(
                    self._apply_events_to_store(
                        staged,
                        [event for event, should_apply in zip(canonical, apply_flags) if should_apply],
                    )
                )
                results = [
                    next(applied) if should_apply else {"ok": True, "duplicate": True}
                    for should_apply in apply_flags
                ]
                with staged_path.open("r", encoding="utf-8") as handle:
                    final_state = json.load(handle)

            final_state["event_seq"] = (existing + new_values)[-1]["seq"]

            atomic_jsonl(self._events_path(session_id), existing + new_values)
            atomic_json(self._state_path(session_id), final_state)
            self._stores[session_id] = StateStore(self._state_path(session_id), logger=self.logger)
            entry = self._entry(session_id)
            refreshed = self._metadata(
                session_id,
                final_state,
                entry["created_at"],
                _utc_now(),
                entry["archived"],
            )
            entry.clear()
            entry.update(refreshed)
            self._persist_catalog()
            return canonical, results

    def snapshot(self, since=0, session_id=None):
        with self._lock:
            session_id, store = self._store(session_id)
            value = store.snapshot(since)
            value["session_id"] = session_id
            return value

    def summary(self, session_id=None):
        """Return session health metadata without copying messages."""
        with self._lock:
            session_id, store = self._store(session_id)
            value = store.summary()
            value["session_id"] = session_id
            return value

    def validate_all(self):
        with self._lock:
            for entry in self._catalog["sessions"]:
                session_id, store = self._store(entry["session_id"])
                store.snapshot(0)
                self._read_events(session_id)
            return True

    def list_sessions(self, include_archived=False):
        with self._lock:
            entries = [
                deepcopy(entry)
                for entry in self._catalog["sessions"]
                if include_archived or not entry["archived"]
            ]
            return {
                "active_session_id": self._catalog["active_session_id"],
                "sessions": entries,
            }

    def create_session(self, title=None, subtitle="", source=None):
        with self._lock:
            state = initial_state()
            if title is not None:
                state["scene"] = validate_scene({"title": title, "subtitle": subtitle})
            session_id = uuid.uuid4().hex
            now = _utc_now()
            state["event_seq"] = 0
            atomic_json(self._state_path(session_id), state)
            self._catalog["sessions"].append(self._metadata(session_id, state, now, now))
            self._catalog["active_session_id"] = session_id
            self._persist_catalog()
            self._stores[session_id] = StateStore(self._state_path(session_id), logger=self.logger)
            event = {
                "type": "conversation.created",
                "source": source or {"host": "manual"},
                "payload": {"title": state["scene"]["title"]},
            }
            canonical, _ = self._emit([event], session_id)
            return {"ok": True, "session": deepcopy(self._entry(session_id)), "event": canonical[0]}

    def select_session(self, session_id, source=None):
        with self._lock:
            entry = self._entry(session_id)
            if entry["archived"]:
                raise ValidationError("session_archived", "restore the session before selecting it", 409)
            self._catalog["active_session_id"] = session_id
            self._persist_catalog()
            canonical, _ = self._emit([{
                "type": "conversation.selected",
                "source": source or {"host": "manual"},
                "payload": {},
            }], session_id)
            return {"ok": True, "active_session_id": session_id, "event": canonical[0]}

    def archive_session(self, session_id, source=None):
        with self._lock:
            if session_id == self._catalog["active_session_id"]:
                raise ValidationError("active_session", "the active session cannot be archived", 409)
            entry = self._entry(session_id)
            if entry["archived"]:
                return {"ok": True, "session": deepcopy(entry)}
            canonical, _ = self._emit([{
                "type": "conversation.archived",
                "source": source or {"host": "manual"},
                "payload": {},
            }], session_id)
            entry = self._entry(session_id)
            entry["archived"] = True
            entry["updated_at"] = _utc_now()
            self._persist_catalog()
            return {"ok": True, "session": deepcopy(entry), "event": canonical[0]}

    def restore_session(self, session_id, source=None):
        with self._lock:
            entry = self._entry(session_id)
            if not entry["archived"]:
                return {"ok": True, "session": deepcopy(entry)}
            entry["archived"] = False
            entry["updated_at"] = _utc_now()
            self._persist_catalog()
            canonical, _ = self._emit([{
                "type": "conversation.restored",
                "source": source or {"host": "manual"},
                "payload": {},
            }], session_id)
            return {"ok": True, "session": deepcopy(self._entry(session_id)), "event": canonical[0]}

    def get_events(self, session_id=None, after=0):
        with self._lock:
            session_id, _ = self._store(session_id)
            if not isinstance(after, int) or after < 0:
                raise ValidationError("invalid_after", "after must be a non-negative integer")
            history = self._read_events(session_id)
            events = [event for event in history if event["seq"] > after]
            return {"session_id": session_id, "events": events, "total": len(history)}

    def request_decision(self, value, session_id=None, source=None):
        if not isinstance(value, dict):
            raise ValidationError("invalid_decision", "decision request must be an object")
        with self._lock:
            session_id, store = self._store(session_id, writable=True)
            provided_created_at = value.get("created_at")
            decision = validate_pending_decision({
                "id": value.get("id") or uuid.uuid4().hex,
                "kind": value.get("kind", "clarification"),
                "prompt": value.get("prompt", ""),
                "options": value.get("options", []),
                "created_at": value.get("created_at") or _utc_now(),
            })
            matching_request = None
            for event in self._read_events(session_id):
                previous = event.get("payload", {}).get("decision")
                if isinstance(previous, dict) and previous.get("id") == decision["id"]:
                    if event["type"] == "decision.requested":
                        matching_request = (event, previous)
                        break
            if matching_request:
                event, previous = matching_request
                if not provided_created_at:
                    decision["created_at"] = previous["created_at"]
                same_session = True
                if "session" in value:
                    proposed = deepcopy(value["session"])
                    proposed["pending_decision"] = decision
                    proposed["status"] = "waiting_user"
                    if decision["kind"] == "plan_approval":
                        proposed.setdefault("workflow", {})["approval"] = "required"
                    normalized_proposed = validate_session(
                        proposed,
                        store.snapshot(0)["participants"],
                    )
                    normalized_previous = validate_session(
                        event.get("payload", {}).get("session"),
                        store.snapshot(0)["participants"],
                    )
                    same_session = normalized_proposed == normalized_previous
                if previous == decision and same_session:
                    return {"ok": True, "duplicate": True, "event": event, "decision": previous}
                raise ValidationError(
                    "decision_conflict", "decision id already has different content", 409
                )
            session = deepcopy(value.get("session", store.snapshot(0)["session"]))
            if session.get("pending_decision") is not None:
                raise ValidationError("decision_pending", "another decision is already pending", 409)
            session["pending_decision"] = decision
            session["status"] = "waiting_user"
            if decision["kind"] == "plan_approval":
                session.setdefault("workflow", {})["approval"] = "required"
            canonical, results = self._emit([{
                "event_id": "decision-request-" + decision["id"],
                "type": "decision.requested",
                "source": source or value.get("source") or {"host": "manual"},
                "payload": {"decision": decision, "session": session},
            }], session_id)
            return {
                "ok": True,
                "event": canonical[0],
                "decision": decision,
                "result": results[0],
            }

    def resolve_decision(self, value, session_id=None, source=None):
        if not isinstance(value, dict):
            raise ValidationError("invalid_decision", "decision resolution must be an object")
        decision_id = _bounded_text(value.get("id", ""), "decision.id", 32)
        if not DECISION_ID.fullmatch(decision_id):
            raise ValidationError(
                "invalid_decision_id", "decision id must be 32 lowercase hex characters"
            )
        action = _bounded_text(value.get("action", ""), "decision.action", 16)
        if action not in DECISION_ACTIONS:
            raise ValidationError(
                "invalid_decision_action",
                "decision action must be one of: %s" % ", ".join(sorted(DECISION_ACTIONS)),
            )
        response = _bounded_text(
            value.get("response", ""), "decision.response", 2000, allow_empty=True
        )
        option_id = _bounded_text(
            value.get("option_id", ""), "decision.option_id", 32, allow_empty=True
        )
        if action in {"edit", "respond"} and not response:
            raise ValidationError(
                "invalid_decision_response", "%s requires a non-empty response" % action
            )
        requested_resolution = {
            "action": action,
            "option_id": option_id,
            "response": response,
        }
        with self._lock:
            session_id, store = self._store(session_id, writable=True)
            history = self._read_events(session_id)
            for event in reversed(history):
                payload = event.get("payload", {})
                previous = payload.get("decision")
                if event["type"] == "decision.resolved" and isinstance(previous, dict):
                    if previous.get("id") != decision_id:
                        continue
                    resolution = payload.get("resolution", {})
                    comparable = {
                        field: resolution.get(field, "")
                        for field in ("action", "option_id", "response")
                    }
                    if comparable == requested_resolution:
                        return {
                            "ok": True,
                            "duplicate": True,
                            "event": event,
                            "resolution": resolution,
                        }
                    raise ValidationError(
                        "decision_conflict", "decision already has a different resolution", 409
                    )
            session = deepcopy(store.snapshot(0)["session"])
            decision = session.get("pending_decision")
            if not decision or decision.get("id") != decision_id:
                raise ValidationError("unknown_decision", "pending decision does not exist", 404)
            option_ids = {option["id"] for option in decision["options"]}
            if option_id and option_id not in option_ids:
                raise ValidationError("invalid_decision_option", "option_id is not offered")
            resolution = dict(requested_resolution, resolved_at=_utc_now())
            session["pending_decision"] = None
            if decision["kind"] == "plan_approval":
                if action == "approve":
                    session["workflow"]["approval"] = "approved"
                    session["status"] = "paused"
                elif action == "reject":
                    session["workflow"]["approval"] = "rejected"
                    session["status"] = "stopped"
                    session["stop_reason"] = response or "plan rejected"
                else:
                    session["workflow"]["approval"] = "required"
                    session["status"] = "paused"
            else:
                has_plan = bool(
                    session.get("objective")
                    and session.get("deliverable")
                    and session.get("roles")
                )
                if action == "reject" and has_plan:
                    session["status"] = "stopped"
                    session["stop_reason"] = response or "decision rejected"
                else:
                    session["status"] = "paused" if has_plan else "idle"
            canonical, results = self._emit([{
                "event_id": "decision-resolve-" + decision_id,
                "type": "decision.resolved",
                "source": source or value.get("source") or {"host": "manual"},
                "payload": {
                    "decision": decision,
                    "resolution": resolution,
                    "session": session,
                },
            }], session_id)
            return {
                "ok": True,
                "event": canonical[0],
                "decision": decision,
                "resolution": resolution,
                "result": results[0],
            }

    def emit_event(self, value, session_id=None):
        events, results = self._emit([value], session_id)
        return {"ok": True, "event": events[0], "result": results[0]}

    def emit_batch(self, values, session_id=None):
        events, results = self._emit(values, session_id)
        return {"ok": True, "events": events, "results": results}

    def add_message(self, payload):
        _, results = self._emit([{"type": "message.created", "payload": payload}])
        return results[0]

    def set_typing(self, payload):
        event_type = "typing.cleared" if payload.get("clear") else "typing.changed"
        _, results = self._emit([{"type": event_type, "payload": payload}])
        return results[0]

    def set_participants(self, participants):
        _, results = self._emit([{
            "type": "participants.replaced",
            "payload": {"participants": participants},
        }])
        return results[0]

    def set_session(self, session):
        _, results = self._emit([{"type": "plan.updated", "payload": {"session": session}}])
        return results[0]

    def set_scene(self, scene):
        _, results = self._emit([{"type": "scene.updated", "payload": {"scene": scene}}])
        return results[0]

    def reset(self, scene=None):
        _, results = self._emit([{"type": "conversation.reset", "payload": {"scene": scene}}])
        return results[0]

    def seed(self, payload):
        _, results = self._emit([{"type": "conversation.seeded", "payload": payload}])
        return results[0]
