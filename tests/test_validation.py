import sys
import unittest
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skill" / "live-chat" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.validation import (  # noqa: E402
    ValidationError,
    validate_message,
    validate_participants,
    validate_persisted_state,
    validate_scene,
    validate_session,
    validate_seed,
    validate_since,
    validate_typing,
)
from live_chat_core.models import initial_state  # noqa: E402


class ValidationTests(unittest.TestCase):
    def session(self, **changes):
        value = {
            "status": "running",
            "background": "已有原型",
            "objective": "选出下一步方案",
            "deliverable": "优先级结论",
            "criteria": ["结论明确", "风险已列出"],
            "roles": [
                {"name": "A", "role": "专家", "focus": "可行性"},
                {"name": "B", "role": "质疑者", "focus": "风险"},
            ],
            "round": {
                "current": 1,
                "max": 3,
                "phase": "independent",
                "completed_participants": [],
            },
            "stop_reason": "",
        }
        value.update(changes)
        return value

    def test_normalizes_message(self):
        value = validate_message({"sender": " Alice ", "text": " hello ", "sys": False}, 1)
        self.assertEqual(value["sender"], "Alice")
        self.assertEqual(value["text"], "hello")
        self.assertEqual(value["id"], 1)

    def test_system_message_allows_empty_sender(self):
        value = validate_message({"text": "Round 2", "sys": True}, 3)
        self.assertEqual(value["sender"], "")

    def test_rejects_non_boolean_typing(self):
        with self.assertRaises(ValidationError) as caught:
            validate_typing({"sender": "Alice", "active": "on"})
        self.assertEqual(caught.exception.code, "invalid_active")

    def test_typing_clear_is_exclusive(self):
        self.assertEqual(validate_typing({"clear": True}), {"clear": True})
        with self.assertRaises(ValidationError):
            validate_typing({"clear": True, "sender": "Alice"})

    def test_scene_and_since_boundaries(self):
        self.assertEqual(validate_scene({"title": " Topic ", "subtitle": ""})["title"], "Topic")
        self.assertEqual(validate_since("12"), 12)
        with self.assertRaises(ValidationError):
            validate_since("-1")

    def test_seed_is_all_or_nothing_and_bounded(self):
        valid = validate_seed({
            "participants": [" A ", "B", "A"],
            "messages": [{"sender": "A", "text": "one"}],
        })
        self.assertEqual(valid["messages"][0]["id"], 1)
        self.assertEqual(valid["participants"], ["A", "B"])
        with self.assertRaises(ValidationError):
            validate_seed({"messages": [{"sender": "A", "text": ""}]})
        with self.assertRaises(ValidationError) as caught:
            validate_seed({"messages": [{"sender": "A", "text": "x"}] * 5001})
        self.assertEqual(caught.exception.status, 413)

    def test_participants_are_ordered_unique_and_bounded(self):
        self.assertEqual(validate_participants([" B ", "A", "B"]), ["B", "A"])
        with self.assertRaises(ValidationError) as caught:
            validate_participants(["P%d" % index for index in range(101)])
        self.assertEqual(caught.exception.code, "too_many_participants")
        self.assertEqual(caught.exception.status, 413)

    def test_old_snapshot_derives_participants_without_changing_schema(self):
        state = deepcopy(initial_state())
        state.pop("participants")
        state["messages"] = [
            {"id": 1, "sender": "A", "text": "one", "sys": False, "ts": ""},
            {"id": 2, "sender": "", "text": "round", "sys": True, "ts": ""},
        ]
        state["typing"] = {"B": True}
        normalized = validate_persisted_state(state)
        self.assertEqual(normalized["participants"], ["A", "B"])
        self.assertEqual(normalized["schema_version"], state["schema_version"])
        self.assertEqual(normalized["session"]["status"], "idle")

    def test_validates_active_session_and_role_references(self):
        normalized = validate_session(self.session(), ["A", "B"])
        self.assertEqual(normalized["round"]["max"], 3)
        self.assertEqual(normalized["roles"][1]["focus"], "风险")
        self.assertEqual(normalized["model_policy"]["default"], "inherit")
        self.assertEqual(normalized["roles"][0]["tone"], "")
        self.assertEqual(normalized["roles"][0]["model"]["requested"], "default")
        with self.assertRaises(ValidationError) as caught:
            validate_session(self.session(), ["A"])
        self.assertEqual(caught.exception.code, "unknown_session_role")

    def test_validates_role_behavior_and_model_resolution_metadata(self):
        value = self.session(model_policy={
            "default": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "fallback": "ask",
        })
        value["roles"][0].update({
            "tone": "理性、简洁",
            "style": "先结论，再给证据",
            "instructions": ["明确假设", "明确假设", "标注风险"],
            "model": {
                "requested": "gpt-5.6-sol",
                "effective": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "fallback_reason": "用户同意使用可用替代",
            },
        })
        normalized = validate_session(value, ["A", "B"])
        role = normalized["roles"][0]
        self.assertEqual(role["tone"], "理性、简洁")
        self.assertEqual(role["style"], "先结论，再给证据")
        self.assertEqual(role["instructions"], ["明确假设", "标注风险"])
        self.assertEqual(role["model"]["requested"], "gpt-5.6-sol")
        self.assertEqual(role["model"]["effective"], "gpt-5.6-terra")
        self.assertEqual(role["model"]["reasoning_effort"], "high")
        self.assertTrue(role["model"]["fallback_reason"])

    def test_rejects_invalid_model_policy_and_role_settings(self):
        invalid_fallback = self.session(model_policy={
            "default": "inherit",
            "reasoning_effort": "medium",
            "fallback": "silent",
        })
        with self.assertRaises(ValidationError) as caught:
            validate_session(invalid_fallback, ["A", "B"])
        self.assertEqual(caught.exception.code, "invalid_model_fallback")

        invalid_effort = self.session()
        invalid_effort["roles"][0]["model"] = {
            "requested": "default",
            "reasoning_effort": "ultra",
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(invalid_effort, ["A", "B"])
        self.assertEqual(caught.exception.code, "invalid_role_reasoning_effort")

        too_many_rules = self.session()
        too_many_rules["roles"][0]["instructions"] = ["rule"] * 11
        with self.assertRaises(ValidationError) as caught:
            validate_session(too_many_rules, ["A", "B"])
        self.assertEqual(caught.exception.code, "invalid_role_instructions")

    def test_rejects_invalid_session_progress(self):
        invalid_round = self.session(round={
            "current": 4,
            "max": 3,
            "phase": "synthesis",
            "completed_participants": [],
        })
        with self.assertRaises(ValidationError):
            validate_session(invalid_round, ["A", "B"])
        invalid_completed = self.session(round={
            "current": 2,
            "max": 3,
            "phase": "challenge",
            "completed_participants": ["C"],
        })
        with self.assertRaises(ValidationError) as caught:
            validate_session(invalid_completed, ["A", "B"])
        self.assertEqual(caught.exception.code, "unknown_completed_participant")


if __name__ == "__main__":
    unittest.main()
