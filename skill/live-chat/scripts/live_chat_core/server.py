"""Local HTTP server for the live-chat UI and API."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import (
    APP_VERSION,
    EVENT_PROTOCOL_VERSION,
    MAX_BODY_BYTES,
    PROTOCOL_VERSION,
    SERVICE_NAME,
)
from .validation import ValidationError, require_mapping, validate_since


class LiveChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, instance_id, asset_path, logger=None):
        super().__init__(address, LiveChatHandler)
        self.store = store
        self.instance_id = instance_id
        self.asset_path = Path(asset_path)
        self.logger = logger or logging.getLogger(__name__)


class LiveChatHandler(BaseHTTPRequestHandler):
    server_version = "LiveChat/2.0"

    def log_message(self, format_string, *args):
        self.server.logger.info("http client=%s " + format_string, self.client_address[0], *args)

    def _headers(self, content_type, length, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _json(self, value, code=200):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._headers("application/json; charset=utf-8", len(body), code)
        self.wfile.write(body)

    def _error(self, code, message, status):
        self._json({"error": {"code": code, "message": message}}, status)

    def _read_json(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise ValidationError("invalid_content_type", "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("invalid_content_length", "Content-Length must be an integer")
        if length < 0:
            raise ValidationError("invalid_content_length", "Content-Length cannot be negative")
        if length > MAX_BODY_BYTES:
            raise ValidationError("body_too_large", "request body exceeds 5 MB", 413)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            raise ValidationError("invalid_json", "request body must be valid UTF-8 JSON")
        return require_mapping(body)

    def _health(self):
        snapshot = self.server.store.snapshot(0)
        value = {
            "ok": True,
            "service": SERVICE_NAME,
            "app_version": APP_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "instance_id": self.server.instance_id,
            "pid": __import__("os").getpid(),
            "epoch": snapshot["epoch"],
            "revision": snapshot["revision"],
        }
        if hasattr(self.server.store, "list_sessions"):
            value.update({
                "event_protocol_version": EVENT_PROTOCOL_VERSION,
                "features": ["sessions", "events", "export", "replay", "doctor", "demo"],
            })
        return value

    def _require_sessions(self):
        if not hasattr(self.server.store, "list_sessions"):
            raise ValidationError(
                "unsupported_feature",
                "this service does not support multi-session operations",
                409,
            )

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            if parsed.path in ("/", "/index.html"):
                try:
                    body = self.server.asset_path.read_bytes()
                except OSError:
                    self._error("asset_missing", "chat.html is unavailable", 500)
                    return
                self._headers("text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if parsed.path == "/api/health":
                self._json(self._health())
                return
            if parsed.path == "/api/state":
                query = parse_qs(parsed.query, keep_blank_values=True)
                since = validate_since(query.get("since", ["0"])[0])
                session_id = query.get("session", [None])[0] or None
                if session_id is not None:
                    self._require_sessions()
                    state = self.server.store.snapshot(since, session_id)
                else:
                    state = self.server.store.snapshot(since)
                state.update({
                    "protocol_version": PROTOCOL_VERSION,
                    "instance_id": self.server.instance_id,
                })
                self._json(state)
                return
            if parsed.path == "/api/sessions":
                self._require_sessions()
                query = parse_qs(parsed.query, keep_blank_values=True)
                include_archived = query.get("include_archived", ["0"])[0] in ("1", "true")
                self._json(self.server.store.list_sessions(include_archived))
                return
            if parsed.path == "/api/events":
                self._require_sessions()
                query = parse_qs(parsed.query, keep_blank_values=True)
                session_id = query.get("session", [None])[0] or None
                after = validate_since(query.get("after", ["0"])[0])
                self._json(self.server.store.get_events(session_id, after))
                return
            self._error("not_found", "endpoint not found", 404)
        except ValidationError as exc:
            self._error(exc.code, exc.message, exc.status)
        except Exception:
            self.server.logger.exception("Unhandled GET failure path=%s", self.path)
            self._error("internal_error", "internal server error", 500)

    def do_POST(self):
        try:
            parsed = urlsplit(self.path)
            body = self._read_json()
            if parsed.path == "/api/msg":
                result = self.server.store.add_message(body)
            elif parsed.path == "/api/typing":
                result = self.server.store.set_typing(body)
            elif parsed.path == "/api/participants":
                result = self.server.store.set_participants(body.get("participants"))
            elif parsed.path == "/api/session":
                if "session" not in body:
                    raise ValidationError(
                        "invalid_session",
                        "request body must contain session",
                    )
                result = self.server.store.set_session(body.get("session"))
            elif parsed.path == "/api/scene":
                result = self.server.store.set_scene(body.get("scene"))
            elif parsed.path == "/api/reset":
                result = self.server.store.reset(body.get("scene"))
            elif parsed.path == "/api/seed":
                result = self.server.store.seed(body)
            elif parsed.path == "/api/sessions":
                self._require_sessions()
                result = self.server.store.create_session(
                    body.get("title"),
                    body.get("subtitle", ""),
                    body.get("source"),
                )
            elif parsed.path == "/api/sessions/select":
                self._require_sessions()
                result = self.server.store.select_session(body.get("session_id"), body.get("source"))
            elif parsed.path == "/api/sessions/archive":
                self._require_sessions()
                result = self.server.store.archive_session(body.get("session_id"), body.get("source"))
            elif parsed.path == "/api/sessions/restore":
                self._require_sessions()
                result = self.server.store.restore_session(body.get("session_id"), body.get("source"))
            elif parsed.path == "/api/events":
                self._require_sessions()
                result = self.server.store.emit_event(body, body.get("session_id"))
            elif parsed.path == "/api/events/batch":
                self._require_sessions()
                events = body.get("events")
                if not isinstance(events, list):
                    raise ValidationError("invalid_event_batch", "events must be an array")
                result = self.server.store.emit_batch(events, body.get("session_id"))
            elif parsed.path == "/api/shutdown":
                result = {"ok": True, "instance_id": self.server.instance_id}
                self._json(result)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self._error("not_found", "endpoint not found", 404)
                return
            self._json(result)
        except ValidationError as exc:
            self._error(exc.code, exc.message, exc.status)
        except Exception:
            self.server.logger.exception("Unhandled POST failure path=%s", self.path)
            self._error("internal_error", "internal server error", 500)
