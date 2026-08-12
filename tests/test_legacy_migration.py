import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skill" / "live-chat" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.store import StateStore  # noqa: E402


class LegacyMigrationTests(unittest.TestCase):
    def test_migrates_copy_without_modifying_source(self):
        with tempfile.TemporaryDirectory(prefix="live-chat-tests-") as directory:
            root = Path(directory)
            legacy = root / "messages.jsonl"
            lines = [
                json.dumps({"type": "scene", "scene": {"title": "Old", "subtitle": "Chat"}}),
                json.dumps({"type": "msg", "msg": {"sender": "A", "text": "Before reset"}}),
                json.dumps({"type": "reset", "scene": {"title": "New", "subtitle": "Chat"}, "epoch": 2}),
                "not-json",
                json.dumps({"type": "msg", "msg": {"sender": "B", "text": "After reset"}}),
            ]
            original = "\n".join(lines) + "\n"
            legacy.write_text(original, encoding="utf-8")
            store = StateStore(root / "state.json", legacy_path=legacy)
            state = store.snapshot(0)
            self.assertEqual(state["scene"]["title"], "New")
            self.assertEqual([message["text"] for message in state["messages"]], ["After reset"])
            self.assertEqual(state["participants"], ["B"])
            self.assertEqual(state["session"]["status"], "idle")
            self.assertEqual(state["epoch"], 2)
            self.assertEqual(legacy.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
