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
    utf8_safe_text,
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

    def test_replaces_lone_surrogates_without_losing_valid_unicode(self):
        self.assertEqual(utf8_safe_text("智能引号\udc9d保留中文"), "智能引号\ufffd保留中文")
        value = validate_message({"sender": "Agent", "text": "reply\udc9d"}, 1)
        self.assertEqual(value["text"], "reply\ufffd")

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

    def test_v1_snapshot_migrates_to_v2_defaults(self):
        state = deepcopy(initial_state())
        state["schema_version"] = 1
        for field in ("workflow", "pending_decision", "run", "result"):
            state["session"].pop(field)
        normalized = validate_persisted_state(state)
        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(normalized["session"]["workflow"]["strategy"], "parallel_panel")
        self.assertEqual(normalized["session"]["workflow"]["approval"], "legacy")
        self.assertIsNone(normalized["session"]["pending_decision"])

    def test_validates_workflow_decision_run_and_result(self):
        value = self.session(status="waiting_user")
        value["workflow"] = {
            "strategy": "critic_revise",
            "approval": "required",
            "limits": {
                "max_rounds": 3,
                "max_participants": 3,
                "max_retries": 1,
                "wall_time_seconds": 900,
            },
        }
        value["pending_decision"] = {
            "id": "b" * 32,
            "kind": "plan_approval",
            "prompt": "是否批准？",
            "options": [{"id": "approve", "label": "批准", "description": "开始执行"}],
            "created_at": "2026-08-15T00:00:00+00:00",
        }
        value["run"] = {
            "id": "run-1",
            "started_at": "2026-08-15T00:00:00+00:00",
            "updated_at": "2026-08-15T00:00:01+00:00",
            "participants": [{"name": "A", "status": "pending", "attempt": 0}],
            "round_summaries": [{
                "round": 1,
                "consensus": ["目标明确"],
                "disagreements": [],
                "evidence": ["message:1"],
                "open_questions": ["等待批准"],
            }],
        }
        value["result"] = {
            "summary": "尚未执行",
            "criteria": [{"text": "结论明确", "status": "unmet", "evidence": []}],
            "disagreements": [],
            "next_actions": ["等待用户"],
        }
        normalized = validate_session(value, ["A", "B"])
        self.assertEqual(normalized["workflow"]["strategy"], "critic_revise")
        self.assertEqual(normalized["pending_decision"]["id"], "b" * 32)
        self.assertEqual(normalized["run"]["participants"][0]["status"], "pending")
        self.assertEqual(normalized["result"]["criteria"][0]["status"], "unmet")

    def test_rejects_pending_decision_outside_waiting_user(self):
        value = self.session()
        value["pending_decision"] = {
            "id": "c" * 32,
            "kind": "checkpoint",
            "prompt": "Continue?",
            "options": [{"id": "yes", "label": "Yes"}],
            "created_at": "",
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "invalid_pending_decision")

    def test_allows_preplan_clarification_without_fabricating_a_plan(self):
        value = {
            "status": "waiting_user",
            "criteria": [],
            "roles": [],
            "round": {
                "current": 0,
                "max": 3,
                "phase": "not_started",
                "completed_participants": [],
            },
            "pending_decision": {
                "id": "f" * 32,
                "kind": "clarification",
                "prompt": "What outcome should the panel deliver?",
                "options": [{"id": "recommendation", "label": "Recommendation"}],
                "created_at": "",
            },
        }
        normalized = validate_session(value, [])
        self.assertEqual(normalized["status"], "waiting_user")
        self.assertEqual(normalized["round"]["current"], 0)
        self.assertEqual(normalized["roles"], [])

    def test_v2_workflow_cannot_run_before_approval(self):
        value = self.session()
        value["workflow"] = {
            "strategy": "parallel_panel",
            "approval": "required",
            "limits": {"max_rounds": 3, "max_participants": 3, "max_retries": 1},
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "approval_required")

        value["workflow"]["approval"] = "legacy"
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "invalid_workflow_approval")

    def test_waiting_v2_workflow_requires_a_persisted_decision(self):
        value = self.session(status="waiting_user")
        value["workflow"] = {
            "strategy": "parallel_panel",
            "approval": "required",
            "limits": {"max_rounds": 3, "max_participants": 3, "max_retries": 1},
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "missing_pending_decision")

    def test_v2_workflow_enforces_round_and_retry_budgets(self):
        value = self.session()
        value["workflow"] = {
            "strategy": "parallel_panel",
            "approval": "approved",
            "limits": {"max_rounds": 2, "max_participants": 2, "max_retries": 0},
        }
        value["round"]["max"] = 2
        value["run"] = {
            "participants": [{"name": "A", "status": "failed", "attempt": 2}],
            "round_summaries": [],
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "retry_budget_exceeded")

        value["run"] = {
            "participants": [{"name": "A", "status": "failed", "attempt": 1}],
            "round_summaries": [{
                "round": 3,
                "consensus": [],
                "disagreements": [],
                "evidence": [],
                "open_questions": [],
            }],
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "round_budget_exceeded")

    def test_completed_v2_workflow_requires_all_criteria_met(self):
        value = self.session(status="completed")
        value["workflow"] = {
            "strategy": "critic_revise",
            "approval": "approved",
            "limits": {"max_rounds": 3, "max_participants": 3, "max_retries": 1},
        }
        value["result"] = {
            "summary": "部分完成",
            "criteria": [
                {"text": "结论明确", "status": "met", "evidence": ["message:1"]},
                {"text": "风险已列出", "status": "partial", "evidence": []},
            ],
            "disagreements": [],
            "next_actions": ["补齐风险"],
        }
        with self.assertRaises(ValidationError) as caught:
            validate_session(value, ["A", "B"])
        self.assertEqual(caught.exception.code, "completion_criteria_unmet")

        value["result"]["criteria"][1] = {
            "text": "风险已列出",
            "status": "met",
            "evidence": ["message:2"],
        }
        self.assertEqual(validate_session(value, ["A", "B"])["status"], "completed")

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
