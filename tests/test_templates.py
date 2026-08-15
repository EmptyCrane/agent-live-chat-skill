import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skill" / "live-chat" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.sessions import SessionStore  # noqa: E402
from live_chat_core.templates import (  # noqa: E402
    prepare_application,
    template_by_id,
    template_catalog,
)
from live_chat_core.validation import ValidationError  # noqa: E402


def application(template_id, request_id, **changes):
    value = {
        "template_id": template_id,
        "template_version": 1,
        "language": "en",
        "request_id": request_id,
        "background": "A bounded test",
        "objective": "Reach a defensible conclusion",
        "deliverable": "A concise recommendation",
        "criteria": ["Visible evidence supports the conclusion"],
        "source": {"host": "codex"},
    }
    value.update(changes)
    return value


def roles(count):
    return [
        {
            "name": "Role %d" % index,
            "role": "Responsibility %d" % index,
            "focus": "Distinct focus %d" % index,
        }
        for index in range(count)
    ]


class TemplateCatalogTests(unittest.TestCase):
    def test_catalog_is_bilingual_unique_and_policy_complete(self):
        english = template_catalog("en")
        chinese = template_catalog("zh-CN")
        self.assertEqual(len(english["templates"]), 10)
        self.assertEqual(
            [item["id"] for item in english["templates"]],
            [item["id"] for item in chinese["templates"]],
        )
        self.assertEqual(len({item["id"] for item in english["templates"]}), 10)
        for item in english["templates"]:
            policy = item["role_policy"]
            self.assertLessEqual(policy["min"], policy["recommended"])
            self.assertGreaterEqual(len(item["core_roles"]), policy["min"])
            if item["category"] == "productivity":
                self.assertIsNotNone(policy["max"])
            else:
                self.assertIsNone(policy["max"])
                self.assertEqual(policy["confirmation_threshold"], 8)

    def test_localization_changes_labels_without_changing_identity(self):
        english = template_by_id("writers_room", "en")
        chinese = template_by_id("writers_room", "zh-CN")
        self.assertEqual(english["id"], chinese["id"])
        self.assertNotEqual(english["name"], chinese["name"])
        self.assertNotEqual(english["core_roles"][0]["name"], chinese["core_roles"][0]["name"])

    def test_default_application_uses_recommended_roster_and_exact_limit(self):
        value = prepare_application(application("architecture_review", "a" * 32))
        self.assertEqual(len(value["participants"]), 4)
        self.assertEqual(value["session"]["workflow"]["limits"]["max_participants"], 4)
        self.assertEqual(value["session"]["workflow"]["dispatch"]["max_concurrent"], 3)
        self.assertEqual(value["waves"], 2)
        self.assertEqual(value["session"]["workflow"]["template"]["version"], 1)

    def test_productivity_limit_and_duplicate_responsibility_are_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            prepare_application(application("content_refinement", "b" * 32, roles=roles(5)))
        self.assertEqual(caught.exception.code, "template_role_limit")
        duplicated = roles(3)
        duplicated[2]["role"] = duplicated[1]["role"]
        duplicated[2]["focus"] = duplicated[1]["focus"]
        with self.assertRaises(ValidationError) as caught:
            prepare_application(application("writers_room", "c" * 32, roles=duplicated))
        self.assertEqual(caught.exception.code, "duplicate_role_responsibility")

    def test_bypass_only_prepares_a_paused_session(self):
        value = prepare_application(
            application("decision_debate", "d" * 32, approval="bypassed")
        )
        self.assertEqual(value["session"]["status"], "paused")
        self.assertEqual(value["session"]["workflow"]["approval"], "bypassed")
        self.assertIsNone(value["decision"])


class TemplateApplicationTests(unittest.TestCase):
    def test_apply_is_atomic_idempotent_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            payload = application("architecture_review", "1" * 32)
            result = store.apply_template(payload)
            self.assertEqual(result["stage"], "plan_approval")
            state = store.snapshot(0)
            self.assertEqual(state["participants"], result["participants"])
            self.assertEqual(state["session"]["status"], "waiting_user")
            self.assertEqual(state["session"]["workflow"]["limits"]["max_participants"], 4)
            duplicate = store.apply_template(payload)
            self.assertTrue(all(item.get("duplicate") for item in duplicate["results"]))
            restarted = SessionStore(directory)
            self.assertEqual(
                restarted.snapshot(0)["session"]["workflow"]["template"]["id"],
                "architecture_review",
            )
            with self.assertRaises(ValidationError) as caught:
                restarted.apply_template(dict(payload, objective="Different objective"))
            self.assertEqual(caught.exception.code, "event_conflict")

    def test_invalid_application_leaves_state_unchanged_and_sessions_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            first_id = store.list_sessions()["active_session_id"]
            second_id = store.create_session("Other")["session"]["session_id"]
            before = store.snapshot(0, first_id)
            with self.assertRaises(ValidationError):
                store.apply_template(
                    application("architecture_review", "2" * 32, criteria=[]),
                    first_id,
                )
            self.assertEqual(store.snapshot(0, first_id), before)
            store.apply_template(application("idea_selection", "3" * 32), second_id)
            self.assertEqual(store.snapshot(0, first_id)["session"]["status"], "idle")
            self.assertEqual(
                store.snapshot(0, second_id)["session"]["workflow"]["template"]["id"],
                "idea_selection",
            )

    def test_large_cast_requires_checkpoint_then_uses_waves(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            cast = roles(9)
            first = store.apply_template(
                application("writers_room", "4" * 32, roles=cast)
            )
            self.assertEqual(first["stage"], "large_cast_confirmation")
            self.assertEqual(first["decision"]["kind"], "checkpoint")
            self.assertEqual(store.snapshot(0)["participants"], [])
            with self.assertRaises(ValidationError) as caught:
                store.apply_template(
                    application(
                        "writers_room",
                        "5" * 32,
                        roles=cast,
                        large_cast_decision_id="4" * 32,
                    )
                )
            self.assertEqual(caught.exception.code, "large_cast_confirmation_required")
            store.resolve_decision({
                "id": "4" * 32,
                "action": "approve",
                "option_id": "continue",
            })
            applied = store.apply_template(
                application(
                    "writers_room",
                    "5" * 32,
                    roles=cast,
                    large_cast_decision_id="4" * 32,
                )
            )
            self.assertEqual(applied["stage"], "plan_approval")
            self.assertEqual(applied["waves"], 3)
            self.assertEqual(
                store.snapshot(0)["session"]["workflow"]["limits"]["max_participants"],
                9,
            )

    def test_hundred_role_technical_boundary(self):
        prepared = prepare_application(
            application(
                "worldbuilding_council",
                "6" * 32,
                roles=roles(100),
                large_cast_decision_id="7" * 32,
            )
        )
        self.assertEqual(len(prepared["participants"]), 100)
        with self.assertRaises(ValidationError) as caught:
            prepare_application(
                application("worldbuilding_council", "8" * 32, roles=roles(101))
            )
        self.assertEqual(caught.exception.code, "too_many_roles")

    def test_cannot_apply_after_dispatch_or_with_pending_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.add_message({"sender": "Agent", "text": "Already started"})
            with self.assertRaises(ValidationError) as caught:
                store.apply_template(application("idea_selection", "9" * 32))
            self.assertEqual(caught.exception.code, "template_not_applicable")


if __name__ == "__main__":
    unittest.main()
