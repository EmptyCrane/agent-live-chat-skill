import json
import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "skill" / "live-chat"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.server import LiveChatHTTPServer  # noqa: E402
from live_chat_core.store import StateStore  # noqa: E402


class ServerTests(unittest.TestCase):
    def session(self, status="running"):
        return {
            "status": status,
            "background": "评审背景",
            "objective": "形成UI决策",
            "deliverable": "改进清单",
            "criteria": ["风险明确"],
            "roles": [
                {"name": "Alice", "role": "产品", "focus": "体验"},
                {"name": "Bob", "role": "工程", "focus": "实现"},
            ],
            "round": {
                "current": 1,
                "max": 3,
                "phase": "independent",
                "completed_participants": [],
            },
            "stop_reason": "",
        }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="live-chat-tests-")
        store = StateStore(Path(self.temp.name) / "state.json")
        self.server = LiveChatHTTPServer(
            ("127.0.0.1", 0), store, "test-instance", ROOT / "assets" / "chat.html"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None, content_type="application/json"):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": content_type} if payload is not None else {}
        request = Request(self.base + path, data=data, headers=headers, method=method)
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_message_and_incremental_state(self):
        _, health = self.request("/api/health")
        self.assertEqual(health["service"], "live-chat")
        self.assertEqual(health["app_version"], "0.1.0-beta.8")
        self.assertEqual(health["instance_id"], "test-instance")
        self.request("/api/msg", "POST", {"sender": "Alice", "text": "One"})
        self.request("/api/msg", "POST", {"sender": "Bob", "text": "Two"})
        _, state = self.request("/api/state?since=1")
        self.assertEqual(state["total"], 2)
        self.assertEqual([message["text"] for message in state["messages"]], ["Two"])

    def test_health_does_not_copy_full_conversation_snapshot(self):
        with patch.object(self.server.store, "snapshot", side_effect=AssertionError):
            _, health = self.request("/api/health")
        self.assertEqual(health["epoch"], 0)
        self.assertEqual(health["revision"], 0)

    def test_typing_clear_and_reset(self):
        self.request("/api/typing", "POST", {"sender": "Alice", "active": True})
        _, cleared = self.request("/api/typing", "POST", {"clear": True})
        self.assertEqual(cleared["typing"], {})
        _, reset = self.request(
            "/api/reset", "POST", {"scene": {"title": "New", "subtitle": "Start"}}
        )
        self.assertEqual(reset["count"], 0)

    def test_participant_roster_endpoint_and_dynamic_append(self):
        _, roster = self.request(
            "/api/participants", "POST", {"participants": ["Waiting", "Alice", "Waiting"]}
        )
        self.assertEqual(roster["participants"], ["Waiting", "Alice"])
        self.request("/api/msg", "POST", {"sender": "Bob", "text": "Joined"})
        _, state = self.request("/api/state")
        self.assertEqual(state["participants"], ["Waiting", "Alice", "Bob"])

    def test_session_endpoint_and_reset(self):
        self.request("/api/participants", "POST", {"participants": ["Alice", "Bob"]})
        _, result = self.request("/api/session", "POST", {"session": self.session()})
        self.assertEqual(result["session"]["status"], "running")
        self.assertEqual(result["session"]["model_policy"]["fallback"], "ask")
        self.assertEqual(result["session"]["roles"][0]["model"]["requested"], "default")
        _, state = self.request("/api/state")
        self.assertEqual(state["session"]["objective"], "形成UI决策")
        self.request("/api/reset", "POST", {"scene": None})
        _, reset_state = self.request("/api/state")
        self.assertEqual(reset_state["session"]["status"], "idle")

    def test_session_endpoint_rejects_missing_wrapper(self):
        self.request("/api/participants", "POST", {"participants": ["Alice", "Bob"]})
        request = Request(
            self.base + "/api/session",
            data=json.dumps(self.session()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(body["error"]["code"], "invalid_session")
        _, state = self.request("/api/state")
        self.assertEqual(state["session"]["status"], "idle")

    def test_structured_validation_error(self):
        request = Request(
            self.base + "/api/msg",
            data=json.dumps({"sender": "", "text": "bad"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(body["error"]["code"], "invalid_sender")

    def test_page_is_served_without_cache(self):
        with urlopen(self.base + "/", timeout=3) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("多智能体群聊直播", body)
        self.assertNotIn("innerHTML", body)
        self.assertNotIn("setInterval(poll", body)
        self.assertNotIn("reset-btn", body)
        self.assertNotIn("method: 'POST'", body)
        self.assertNotIn("<canvas", body)
        self.assertNotIn("backdrop-filter", body)
        self.assertNotIn("filter:", body)
        self.assertIn("document.createDocumentFragment()", body)
        self.assertIn("prefers-color-scheme: dark", body)
        self.assertIn("participant-rail", body)
        self.assertIn("member-sheet", body)
        self.assertIn("live-chat-theme", body)
        self.assertIn("等待发言", body)
        self.assertIn("data.participants", body)
        self.assertIn("data.session", body)
        self.assertIn("session-bar", body)
        self.assertIn("等待用户", body)
        self.assertIn("roleModelText", body)
        self.assertIn("替代说明", body)
        self.assertIn("const TRANSLATIONS", body)
        self.assertIn("Live multi-agent group chat", body)
        self.assertIn("URLSearchParams(window.location.search)", body)
        self.assertIn("document.documentElement.lang = locale", body)
        self.assertIn("rail-session-select", body)
        self.assertIn("/api/sessions?include_archived=1", body)
        self.assertIn("encodeURIComponent(selectedSession)", body)
        self.assertIn("new EventSource('/api/stream", body)
        self.assertIn("message-search", body)
        self.assertIn("compareWithActive", body)
        self.assertIn("/api/templates?lang=", body)
        self.assertIn("templateVersion", body)
        self.assertIn("concurrent", body)

    def test_large_history_seed_and_incremental_read(self):
        messages = [
            {"sender": "Agent %d" % (index % 12), "text": "Message %d" % index}
            for index in range(500)
        ]
        roster = ["Agent %d" % index for index in range(12)]
        _, seeded = self.request(
            "/api/seed", "POST", {"participants": roster, "messages": messages}
        )
        self.assertEqual(seeded["count"], 500)
        _, tail = self.request("/api/state?since=495")
        self.assertEqual(tail["total"], 500)
        self.assertEqual(len(tail["messages"]), 5)
        self.assertEqual(tail["participants"], roster)

    def test_skill_declares_builtin_browser_open_and_safe_fallback(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        hosts = (ROOT / "references" / "hosts.md").read_text(encoding="utf-8")
        self.assertIn("codex_app__open_in_codex", hosts)
        self.assertIn('target: {type: "browser", url: "<start returned URL>"}', hosts)
        self.assertIn('placement: "right"', hosts)
        self.assertIn("including delayed tools", skill + hosts)
        self.assertIn("initial tool summary", skill + hosts)
        self.assertIn("Do not consider the chat display started", skill)
        self.assertIn("Never invoke the system default browser", skill + hosts)
        self.assertIn("never replaces Codex built-in-browser acceptance", hosts)
        self.assertIn("--json start", skill)
        self.assertIn("Never invoke the system default browser", skill)
        self.assertNotIn('"width"', skill)
        self.assertNotIn('"height"', skill)
        self.assertIn("Check host capabilities", skill)
        self.assertIn("approved roster size", skill)
        self.assertIn("references/templates.md", skill)
        self.assertIn("continue in waves", skill)
        self.assertIn("waiting_user", skill)
        self.assertIn("completed_participants", skill)

    def test_rejects_oversized_body(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=3
        )
        try:
            connection.putrequest("POST", "/api/msg")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(5 * 1024 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
