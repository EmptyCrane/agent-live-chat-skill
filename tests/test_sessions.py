import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
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

    def test_reads_v1_events_and_appends_v2_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root)
            session_id = store.list_sessions()["active_session_id"]
            events_path = root / "sessions" / session_id / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            events[0]["event_version"] = 1
            events_path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            reopened = SessionStore(root)
            reopened.add_message({"sender": "Agent", "text": "v2 append"})
            versions = [
                json.loads(line)["event_version"]
                for line in events_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(versions, [1, 2])
            self.assertTrue(reopened.validate_all())

    def test_edit_reject_and_respond_decision_actions_are_persisted(self):
        draft = {
            "status": "waiting_user",
            "objective": "approve a bounded plan",
            "deliverable": "a user-approved plan",
            "criteria": ["user decision is persisted"],
            "roles": [
                {"name": "A", "role": "lead", "focus": "quality"},
                {"name": "B", "role": "critic", "focus": "risk"},
            ],
            "round": {
                "current": 1,
                "max": 3,
                "phase": "independent",
                "completed_participants": [],
            },
        }
        for action, expected_status, expected_approval in (
            ("edit", "paused", "required"),
            ("reject", "stopped", "rejected"),
        ):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(directory)
                store.set_participants(["A", "B"])
                decision_id = ("a" if action == "edit" else "b") * 32
                store.request_decision({
                    "id": decision_id,
                    "kind": "plan_approval",
                    "prompt": "Approve?",
                    "options": [{"id": action, "label": action.title()}],
                    "session": draft,
                })
                resolved = store.resolve_decision({
                    "id": decision_id,
                    "action": action,
                    "option_id": action,
                    "response": "change the roles" if action == "edit" else "not approved",
                })
                session = store.snapshot(0)["session"]
                self.assertEqual(session["status"], expected_status)
                self.assertEqual(session["workflow"]["approval"], expected_approval)
                self.assertEqual(resolved["resolution"]["action"], action)

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.request_decision({
                "id": "c" * 32,
                "kind": "clarification",
                "prompt": "Which deliverable?",
                "options": [{"id": "report", "label": "Report"}],
            })
            with self.assertRaises(ValidationError) as caught:
                store.resolve_decision({"id": "c" * 32, "action": "respond"})
            self.assertEqual(caught.exception.code, "invalid_decision_response")
            store.resolve_decision({
                "id": "c" * 32,
                "action": "respond",
                "option_id": "report",
                "response": "Deliver a concise report",
            })
            self.assertEqual(store.snapshot(0)["session"]["status"], "idle")


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
        self.assertEqual(health["event_protocol_version"], 2)
        self.assertEqual(health["session_schema_version"], 2)
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

    def test_decision_lifecycle_is_idempotent_and_session_isolated(self):
        created = self.request("/api/sessions", "POST", {"title": "Decision session"})
        session_id = created["session"]["session_id"]
        self.request("/api/participants", "POST", {"participants": ["Alice", "Bob"]})
        draft = {
            "status": "waiting_user",
            "objective": "approve a plan",
            "deliverable": "approved plan",
            "criteria": ["user decides"],
            "roles": [
                {"name": "Alice", "role": "lead", "focus": "quality"},
                {"name": "Bob", "role": "critic", "focus": "risk"},
            ],
            "round": {"current": 1, "max": 3, "phase": "independent", "completed_participants": []},
        }
        requested = self.request("/api/decisions", "POST", {
            "session_id": session_id,
            "id": "a" * 32,
            "kind": "plan_approval",
            "prompt": "Approve this plan?",
            "options": [
                {"id": "approve", "label": "Approve"},
                {"id": "edit", "label": "Edit"},
            ],
            "session": draft,
        })
        self.assertEqual(requested["decision"]["id"], "a" * 32)
        duplicate_request = self.request("/api/decisions", "POST", {
            "session_id": session_id,
            "id": "a" * 32,
            "kind": "plan_approval",
            "prompt": "Approve this plan?",
            "options": [
                {"id": "approve", "label": "Approve"},
                {"id": "edit", "label": "Edit"},
            ],
            "session": draft,
        })
        self.assertTrue(duplicate_request["duplicate"])
        resolved = self.request("/api/decisions/resolve", "POST", {
            "session_id": session_id,
            "id": "a" * 32,
            "action": "approve",
            "option_id": "approve",
        })
        self.assertEqual(resolved["resolution"]["action"], "approve")
        duplicate = self.request("/api/decisions/resolve", "POST", {
            "session_id": session_id,
            "id": "a" * 32,
            "action": "approve",
            "option_id": "approve",
        })
        self.assertTrue(duplicate["duplicate"])
        state = self.request("/api/state?session=" + session_id)
        self.assertEqual(state["session"]["status"], "paused")
        self.assertEqual(state["session"]["workflow"]["approval"], "approved")
        self.assertIsNone(state["session"]["pending_decision"])
        duplicate_after_resolve = self.request("/api/decisions", "POST", {
            "session_id": session_id,
            "id": "a" * 32,
            "kind": "plan_approval",
            "prompt": "Approve this plan?",
            "options": [
                {"id": "approve", "label": "Approve"},
                {"id": "edit", "label": "Edit"},
            ],
            "session": draft,
        })
        self.assertTrue(duplicate_after_resolve["duplicate"])

        other_id = self.request("/api/sessions", "POST", {"title": "Unrelated"})["session"]["session_id"]
        other = self.request("/api/state?session=" + other_id)
        self.assertIsNone(other["session"]["pending_decision"])
        self.assertEqual(other["session"]["status"], "idle")

    def test_decisions_reject_invalid_ids_and_conflicting_content(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            with self.assertRaises(ValidationError) as caught:
                store.resolve_decision({"id": "not-hex", "action": "approve"})
            self.assertEqual(caught.exception.code, "invalid_decision_id")
            first = {
                "id": "e" * 32,
                "kind": "clarification",
                "prompt": "Choose one",
                "options": [{"id": "one", "label": "One"}],
            }
            store.request_decision(first)
            with self.assertRaises(ValidationError) as caught:
                store.request_decision(dict(first, prompt="Different"))
            self.assertEqual(caught.exception.code, "decision_conflict")

    def test_sse_stream_announces_revision_without_posting_from_page(self):
        self.request("/api/msg", "POST", {"sender": "Agent", "text": "wake stream"})
        with urlopen(self.base + "/api/stream?after_revision=0", timeout=3) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
            lines = [response.readline().decode("utf-8").strip() for _ in range(3)]
        self.assertIn("event: revision", lines)
        self.assertTrue(any(line.startswith("data: ") for line in lines))

    def test_sse_unknown_session_returns_structured_error_before_stream_headers(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/stream?session=" + ("f" * 32), timeout=3)
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(caught.exception.headers["Content-Type"], "application/json; charset=utf-8")
        payload = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "unknown_session")


if __name__ == "__main__":
    unittest.main()
