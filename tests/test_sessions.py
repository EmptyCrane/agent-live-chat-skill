import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skill" / "live-chat" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.sessions import SessionStore  # noqa: E402
from live_chat_core.server import LiveChatHTTPServer  # noqa: E402
from live_chat_core.store import StateStore  # noqa: E402
from live_chat_core.validation import ValidationError  # noqa: E402


class SessionStoreTests(unittest.TestCase):
    def test_beta4_snapshot_is_imported_once_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = StateStore(root / "state.json")
            legacy.add_message({"sender": "Alice", "text": "kept"})
            before = hashlib.sha256((root / "state.json").read_bytes()).hexdigest()
            first = SessionStore(root)
            session_id = first.list_sessions()["active_session_id"]
            self.assertEqual(first.snapshot(0)["messages"][0]["text"], "kept")
            self.assertEqual(hashlib.sha256((root / "state.json").read_bytes()).hexdigest(), before)
            second = SessionStore(root)
            self.assertEqual(second.list_sessions()["active_session_id"], session_id)
            self.assertEqual(len(second.list_sessions(True)["sessions"]), 1)

    def test_create_select_archive_restore_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            original = store.list_sessions()["active_session_id"]
            created = store.create_session("第二场", "测试")
            second = created["session"]["session_id"]
            store.add_message({"sender": "测试员", "text": "独立历史"})
            with self.assertRaises(ValidationError):
                store.archive_session(second)
            store.select_session(original)
            archived = store.archive_session(second)
            self.assertTrue(archived["session"]["archived"])
            self.assertEqual(store.snapshot(0, second)["messages"][0]["text"], "独立历史")
            with self.assertRaises(ValidationError):
                store.select_session(second)
            store.restore_session(second)
            store.select_session(second)
            self.assertEqual(store.list_sessions()["active_session_id"], second)

    def test_events_are_idempotent_and_batch_is_all_or_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            event = {
                "event_id": "fixed-event",
                "type": "message.created",
                "source": {"host": "codex", "actor": "Agent"},
                "payload": {"sender": "Agent", "text": "once"},
            }
            store.emit_event(event)
            store.emit_event(event)
            self.assertEqual(len(store.snapshot(0)["messages"]), 1)
            with self.assertRaises(ValidationError):
                store.emit_event(dict(event, payload={"sender": "Agent", "text": "different"}))
            before_events = store.get_events()["total"]
            with self.assertRaises(ValidationError):
                store.emit_batch([
                    {"type": "message.created", "payload": {"sender": "Agent", "text": "valid"}},
                    {"type": "message.created", "payload": {"sender": "Agent", "text": ""}},
                ])
            self.assertEqual(len(store.snapshot(0)["messages"]), 1)
            self.assertEqual(store.get_events()["total"], before_events)

    def test_duplicate_inside_one_batch_applies_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            event = {
                "event_id": "same-batch-id",
                "type": "message.created",
                "payload": {"sender": "Agent", "text": "one mutation"},
            }
            store.emit_batch([event, event])
            self.assertEqual([message["text"] for message in store.snapshot(0)["messages"]], ["one mutation"])

    def test_batch_persists_staged_snapshot_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            events = [
                {
                    "type": "message.created",
                    "payload": {"sender": "Agent", "text": "message %d" % index},
                }
                for index in range(50)
            ]
            writes = []
            original = StateStore._persist

            def tracked_persist(instance, state):
                writes.append(instance.path)
                return original(instance, state)

            with patch.object(StateStore, "_persist", tracked_persist):
                result = store.emit_batch(events)
            self.assertEqual(len(result["events"]), 50)
            self.assertEqual(len(writes), 1)
            self.assertEqual(len(store.snapshot(0)["messages"]), 50)

    def test_batch_rejects_an_invalid_intermediate_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.set_participants(["Alice", "Bob"])
            store.set_session({
                "status": "running",
                "objective": "keep role references valid",
                "deliverable": "a valid plan",
                "criteria": ["roles remain registered"],
                "roles": [
                    {"name": "Alice", "role": "lead", "focus": "design"},
                    {"name": "Bob", "role": "reviewer", "focus": "risk"},
                ],
                "round": {
                    "current": 1,
                    "max": 3,
                    "phase": "independent",
                    "completed_participants": [],
                },
            })
            before = store.snapshot(0)
            with self.assertRaises(ValidationError):
                store.emit_batch([
                    {
                        "type": "participants.replaced",
                        "payload": {"participants": ["Alice"]},
                    },
                    {"type": "plan.updated", "payload": {"session": None}},
                ])
            after = store.snapshot(0)
            self.assertEqual(after["participants"], before["participants"])
            self.assertEqual(after["session"], before["session"])

    def test_replays_events_after_snapshot_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root)
            session_id = store.list_sessions()["active_session_id"]
            store.add_message({"sender": "Agent", "text": "recover me"})
            state_path = root / "sessions" / session_id / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["messages"] = []
            state["participants"] = []
            state["event_seq"] = 1
            state_path.write_text(json.dumps(state), encoding="utf-8")
            recovered = SessionStore(root)
            self.assertEqual(recovered.snapshot(0)["messages"][0]["text"], "recover me")
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["event_seq"], 2)


class MultiSessionServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="live-chat-session-http-")
        store = SessionStore(self.temp.name)
        root = REPO_ROOT / "skill" / "live-chat"
        self.server = LiveChatHTTPServer(
            ("127.0.0.1", 0), store, "session-test", root / "assets" / "chat.html"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = Request(self.base + path, data=data, headers=headers, method=method)
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_http_sessions_events_and_legacy_endpoints_share_state(self):
        health = self.request("/api/health")
        self.assertEqual(health["protocol_version"], 1)
        self.assertEqual(health["event_protocol_version"], 1)
        created = self.request("/api/sessions", "POST", {"title": "API session"})
        session_id = created["session"]["session_id"]
        self.request("/api/msg", "POST", {"sender": "Legacy client", "text": "compatible"})
        self.request("/api/events", "POST", {
            "session_id": session_id,
            "type": "message.created",
            "source": {"host": "copilot"},
            "payload": {"sender": "Event client", "text": "normalized"},
        })
        state = self.request("/api/state?since=0&session=" + session_id)
        self.assertEqual([message["text"] for message in state["messages"]], ["compatible", "normalized"])
        history = self.request("/api/events?after=0&session=" + session_id)
        self.assertEqual(history["events"][-1]["source"]["host"], "copilot")


if __name__ == "__main__":
    unittest.main()
