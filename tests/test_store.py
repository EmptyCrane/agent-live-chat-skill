import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skill" / "live-chat" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.store import StateStore  # noqa: E402
from live_chat_core.validation import ValidationError  # noqa: E402
from live_chat_core.models import initial_state  # noqa: E402


class StoreTests(unittest.TestCase):
    def session(self, status="running"):
        return {
            "status": status,
            "background": "已有数据",
            "objective": "形成结论",
            "deliverable": "一份决策摘要",
            "criteria": ["两种观点已比较"],
            "roles": [
                {"name": "A", "role": "正方", "focus": "收益"},
                {"name": "B", "role": "反方", "focus": "风险"},
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
        self.path = Path(self.temp.name) / "state.json"
        self.store = StateStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_and_recovers_all_state(self):
        self.store.set_scene({"title": "Review", "subtitle": "Round one"})
        self.store.set_typing({"sender": "Alice", "active": True})
        self.store.add_message({"sender": "Alice", "text": "Hello"})
        recovered = StateStore(self.path).snapshot(0)
        self.assertEqual(recovered["scene"]["title"], "Review")
        self.assertEqual(recovered["messages"][0]["text"], "Hello")
        self.assertTrue(recovered["typing"]["Alice"])
        self.assertEqual(recovered["revision"], 3)

    def test_load_atomically_migrates_v1_snapshot_to_v2(self):
        legacy = initial_state()
        legacy["schema_version"] = 1
        for field in ("workflow", "pending_decision", "run", "result"):
            legacy["session"].pop(field)
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        StateStore(self.path)
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["schema_version"], 2)
        self.assertEqual(persisted["session"]["workflow"]["approval"], "legacy")

    def test_reset_and_seed_advance_epoch(self):
        self.store.set_participants(["Waiting", "A"])
        before = self.store.snapshot(0)
        reset = self.store.reset({"title": "New", "subtitle": ""})
        self.assertEqual(reset["epoch"], before["epoch"] + 1)
        self.assertEqual(self.store.snapshot(0)["participants"], ["Waiting", "A"])
        seeded = self.store.seed({"messages": [{"sender": "B", "text": "Imported"}]})
        self.assertEqual(seeded["epoch"], reset["epoch"] + 1)
        self.assertEqual(self.store.snapshot(0)["messages"][0]["id"], 1)
        self.assertEqual(self.store.snapshot(0)["participants"], ["B"])

    def test_explicit_roster_and_dynamic_members(self):
        result = self.store.set_participants(["Waiting", "Alice", "Waiting"])
        self.assertEqual(result["participants"], ["Waiting", "Alice"])
        self.store.set_typing({"sender": "Bob", "active": True})
        self.store.add_message({"sender": "Carol", "text": "Hello"})
        self.store.add_message({"sys": True, "text": "Round"})
        self.assertEqual(
            self.store.snapshot(0)["participants"],
            ["Waiting", "Alice", "Bob", "Carol"],
        )
        self.store.set_participants([])
        self.assertEqual(self.store.snapshot(0)["participants"], [])

    def test_seed_can_keep_waiting_participants(self):
        self.store.seed({
            "participants": ["A", "B", "C"],
            "messages": [{"sender": "A", "text": "Only A spoke"}],
        })
        self.assertEqual(self.store.snapshot(0)["participants"], ["A", "B", "C"])

    def test_session_persists_and_reset_returns_to_idle(self):
        self.store.set_participants(["A", "B"])
        session = self.session()
        session["model_policy"] = {
            "default": "balanced-model",
            "reasoning_effort": "medium",
            "fallback": "ask",
        }
        session["roles"][0].update({
            "tone": "直接但尊重",
            "style": "先结论后证据",
            "instructions": ["标注假设"],
            "model": {
                "requested": "quality-model",
                "effective": "quality-model",
                "reasoning_effort": "high",
                "fallback_reason": "",
            },
        })
        self.store.set_session(session)
        recovered = StateStore(self.path).snapshot(0)
        self.assertEqual(recovered["session"]["objective"], "形成结论")
        self.assertEqual(recovered["session"]["model_policy"]["default"], "balanced-model")
        self.assertEqual(recovered["session"]["roles"][0]["tone"], "直接但尊重")
        self.assertEqual(
            recovered["session"]["roles"][0]["model"]["effective"], "quality-model"
        )
        self.store.reset()
        state = self.store.snapshot(0)
        self.assertEqual(state["session"]["status"], "idle")
        self.assertEqual(state["participants"], ["A", "B"])

    def test_seed_session_adds_waiting_role_names(self):
        self.store.seed({"session": self.session(), "messages": []})
        state = self.store.snapshot(0)
        self.assertEqual(state["participants"], ["A", "B"])
        self.assertEqual(state["session"]["status"], "running")

    def test_active_session_prevents_orphaned_roles(self):
        self.store.set_participants(["A", "B"])
        self.store.set_session(self.session())
        before = self.store.snapshot(0)
        with self.assertRaises(ValidationError):
            self.store.set_participants(["A"])
        self.assertEqual(self.store.snapshot(0), before)

    def test_paused_session_restores_completed_participants(self):
        self.store.set_participants(["A", "B"])
        paused = self.session("paused")
        paused["round"] = {
            "current": 2,
            "max": 3,
            "phase": "challenge",
            "completed_participants": ["A"],
        }
        paused["stop_reason"] = "用户要求暂停"
        self.store.set_session(paused)
        recovered = StateStore(self.path).snapshot(0)["session"]
        self.assertEqual(recovered["status"], "paused")
        self.assertEqual(recovered["round"]["completed_participants"], ["A"])

    def test_accepts_terminal_and_waiting_states(self):
        self.store.set_participants(["A", "B"])
        for status in ("waiting_user", "completed", "stopped", "partial_failure"):
            value = self.session(status)
            value["round"]["current"] = 3
            value["round"]["phase"] = "synthesis"
            value["stop_reason"] = "状态验收"
            self.store.set_session(value)
            self.assertEqual(self.store.snapshot(0)["session"]["status"], status)

    def test_invalid_seed_does_not_mutate_state(self):
        self.store.add_message({"sender": "A", "text": "Keep me"})
        before = self.store.snapshot(0)
        with self.assertRaises(ValidationError):
            self.store.seed({"messages": [{"sender": "A", "text": ""}]})
        self.assertEqual(self.store.snapshot(0), before)

    def test_corrupt_snapshot_is_not_overwritten(self):
        corrupt_path = Path(self.temp.name) / "corrupt.json"
        corrupt_path.write_text("{broken", encoding="utf-8")
        before = corrupt_path.read_bytes()
        with self.assertRaises(RuntimeError):
            StateStore(corrupt_path)
        self.assertEqual(corrupt_path.read_bytes(), before)

    def test_clear_typing(self):
        self.store.set_typing({"sender": "A", "active": True})
        self.store.set_typing({"sender": "B", "active": True})
        result = self.store.set_typing({"clear": True})
        self.assertEqual(result["typing"], {})

    def test_concurrent_messages_have_unique_ordered_ids(self):
        def push(index):
            return self.store.add_message({"sender": "Agent", "text": "m%d" % index})["id"]

        with ThreadPoolExecutor(max_workers=12) as pool:
            ids = list(pool.map(push, range(100)))
        self.assertEqual(sorted(ids), list(range(1, 101)))
        snapshot = self.store.snapshot(0)
        self.assertEqual([m["id"] for m in snapshot["messages"]], list(range(1, 101)))
        with self.path.open("r", encoding="utf-8") as handle:
            persisted = json.load(handle)
        self.assertEqual(len(persisted["messages"]), 100)


if __name__ == "__main__":
    unittest.main()
