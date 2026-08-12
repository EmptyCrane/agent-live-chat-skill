import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "skill" / "live-chat"
ENTRY = ROOT / "scripts" / "live_chat.py"
sys.path.insert(0, str(ROOT / "scripts"))

from live_chat_core import cli  # noqa: E402

SKIP_PROCESS_TESTS = os.environ.get("LIVE_CHAT_SKIP_PROCESS_TESTS") == "1"
PROCESS_SKIP_REASON = (
    "hosted macOS runners currently close cross-process Python loopback listeners "
    "(actions/runner-images#14409)"
)


class FakeResponse:
    def __init__(self, status, reason, value):
        self.status = status
        self.reason = reason
        self._body = json.dumps(value).encode("utf-8")

    def read(self):
        return self._body


class FakeConnection:
    def __init__(self, responses):
        self.responses = iter(responses)

    def request(self, method, target, body=None, headers=None):
        pass

    def getresponse(self):
        return next(self.responses)

    def close(self):
        pass


class CliTests(unittest.TestCase):
    def session_json(self):
        return json.dumps({
            "status": "running",
            "background": "CLI测试",
            "objective": "验证会话CLI",
            "deliverable": "测试结果",
            "criteria": ["状态可读取"],
            "roles": [
                {"name": "Alice", "role": "发言者", "focus": "功能"},
                {"name": "Waiting", "role": "观察者", "focus": "恢复"},
            ],
            "round": {
                "current": 1,
                "max": 3,
                "phase": "independent",
                "completed_participants": [],
            },
            "stop_reason": "",
        }, ensure_ascii=False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="live-chat-tests-")
        self.state_dir = Path(self.temp.name) / "runtime"
        self.environment = os.environ.copy()
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.environment["PYTHONUTF8"] = "1"
        self.environment["PYTHONIOENCODING"] = "utf-8"
        # Local control calls must not inherit a host or CI proxy configuration.
        self.environment["HTTP_PROXY"] = "http://127.0.0.1:9"
        self.environment["HTTPS_PROXY"] = "http://127.0.0.1:9"
        self.environment["ALL_PROXY"] = "http://127.0.0.1:9"
        self.environment["NO_PROXY"] = ""
        self.environment["no_proxy"] = ""

    def tearDown(self):
        self.run_cli("stop", check=False)
        self.temp.cleanup()

    def run_cli(self, *arguments, input_text=None, check=True, cwd=None):
        command = [
            sys.executable,
            "-B",
            str(ENTRY),
            "--state-dir",
            str(self.state_dir),
        ] + list(arguments)
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=self.environment,
            cwd=cwd,
            timeout=15,
        )
        if check and result.returncode:
            log_file = self.state_dir / "server.log"
            try:
                server_log = log_file.read_text(encoding="utf-8")
            except OSError:
                server_log = "<unavailable>"
            self.fail(
                "CLI failed (%s)\nstdout:\n%s\nstderr:\n%s\nserver.log:\n%s"
                % (result.returncode, result.stdout, result.stderr, server_log)
            )
        return result

    def test_http_error_shapes_are_normalized(self):
        cases = [
            ({"error": {"message": "structured failure"}}, "structured failure"),
            ({"error": "not found"}, "not found"),
            ({}, "418 Teapot"),
            ({"error": ["bad"]}, "418 Teapot"),
            ({"error": 7}, "418 Teapot"),
            ({"error": None}, "418 Teapot"),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                connection = FakeConnection([FakeResponse(418, "Teapot", payload)])
                with patch.object(cli, "HTTPConnection", return_value=connection):
                    with self.assertRaises(cli.CliError) as caught:
                        cli._request_json("http://127.0.0.1:8765/api/test")
                self.assertIn(expected, str(caught.exception))

    def test_pre_beta_string_error_has_no_traceback(self):
        connection = FakeConnection([
            FakeResponse(404, "Not Found", {"error": "not found"}),
        ])
        stderr = io.StringIO()
        with patch.object(cli, "HTTPConnection", return_value=connection):
            with contextlib.redirect_stderr(stderr):
                result = cli.main(["--url", "http://127.0.0.1:8765", "sessions", "list"])
        self.assertNotEqual(result, 0)
        self.assertIn("not found", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertNotIn("AttributeError", stderr.getvalue())

    def test_beta4_feature_probe_still_reports_unsupported(self):
        health = {
            "service": "live-chat",
            "protocol_version": cli.PROTOCOL_VERSION,
            "features": [],
        }
        connection = FakeConnection([FakeResponse(200, "OK", health)])
        stderr = io.StringIO()
        with patch.object(cli, "HTTPConnection", return_value=connection):
            with contextlib.redirect_stderr(stderr):
                result = cli.main(["--url", "http://127.0.0.1:8765", "sessions", "list"])
        self.assertNotEqual(result, 0)
        self.assertIn("unsupported_feature", stderr.getvalue())

    def test_beta5_success_response_is_unchanged(self):
        health = {
            "service": "live-chat",
            "protocol_version": cli.PROTOCOL_VERSION,
            "features": ["sessions"],
        }
        catalog = {"active_session_id": "session-1", "sessions": []}
        connection = FakeConnection([
            FakeResponse(200, "OK", health),
            FakeResponse(200, "OK", catalog),
        ])
        stdout = io.StringIO()
        with patch.object(cli, "HTTPConnection", return_value=connection):
            with contextlib.redirect_stdout(stdout):
                result = cli.main([
                    "--url", "http://127.0.0.1:8765", "--json", "sessions", "list"
                ])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue()), catalog)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_full_lifecycle_and_stdin_message(self):
        started = self.run_cli("--json", "start", "--port", "0", "--no-legacy")
        instance = json.loads(started.stdout)
        self.assertTrue(instance["url"].startswith("http://127.0.0.1:"))
        self.assertEqual(instance["app_version"], "0.1.0-beta.6")
        self.run_cli("msg", "Alice", "--stdin", input_text="Line one\nLine two")
        self.run_cli("participants", "set", "Alice", "Waiting", "Alice")
        self.run_cli("session", "set", "--stdin", input_text=self.session_json())
        status = self.run_cli("--json", "status")
        value = json.loads(status.stdout)
        self.assertEqual(value["app_version"], "0.1.0-beta.6")
        self.assertTrue(value["active_session_id"])
        self.assertEqual(len(value["sessions"]), 1)
        self.assertEqual(value["messages"], 1)
        self.assertEqual(value["participants"], ["Alice", "Waiting"])
        self.assertEqual(value["session"]["status"], "running")
        self.assertEqual(value["session"]["round"]["current"], 1)
        self.assertEqual(value["typing"], [])
        stopped = self.run_cli("stop")
        self.assertIn("服务已停止", stopped.stdout)
        self.run_cli("start", "--port", "0", "--no-legacy")
        recovered = json.loads(self.run_cli("--json", "status").stdout)
        self.assertEqual(recovered["messages"], 1)
        self.run_cli("reset", "Recovered", "New round")
        after_reset = json.loads(self.run_cli("--json", "status").stdout)
        self.assertEqual(after_reset["messages"], 0)
        self.assertEqual(after_reset["participants"], ["Alice", "Waiting"])
        self.assertEqual(after_reset["session"]["status"], "idle")
        self.run_cli("participants", "clear")
        self.assertEqual(json.loads(self.run_cli("--json", "status").stdout)["participants"], [])

    def test_rejects_ambiguous_message_sources(self):
        result = self.run_cli("msg", "Alice", "text", "--stdin", input_text="other", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provide message text", result.stderr)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_participants_set_requires_a_name(self):
        self.run_cli("start", "--port", "0", "--no-legacy")
        result = self.run_cli("participants", "set", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires at least one name", result.stderr)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_session_set_requires_exactly_one_source(self):
        self.run_cli("start", "--port", "0", "--no-legacy")
        result = self.run_cli("session", "set", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires exactly one", result.stderr)

    def test_doctor_returns_structured_warning_without_service(self):
        result = self.run_cli("--json", "doctor", "--host", "generic", "--port", "0", check=False)
        value = json.loads(result.stdout)
        self.assertTrue(value["ok"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(value["status"], "warn")
        self.assertEqual(value["exit_code"], 2)
        self.assertIn("python", {check["id"] for check in value["checks"]})
        self.assertIn("state_directory", {check["id"] for check in value["checks"]})

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_doctor_checks_running_service_and_corrupt_catalog(self):
        project = Path(self.temp.name) / "project"
        (project / ".agents" / "skills" / "live-chat").mkdir(parents=True)
        (project / ".agents" / "skills" / "live-chat" / "SKILL.md").write_text(
            "---\nname: live-chat\n---\n", encoding="utf-8"
        )
        self.run_cli("start", "--port", "0", "--no-legacy")
        healthy = self.run_cli(
            "--json", "doctor", "--host", "generic", "--scope", "project", "--port", "0", cwd=project
        )
        healthy_value = json.loads(healthy.stdout)
        self.assertEqual(healthy_value["status"], "pass")
        self.assertEqual(healthy_value["exit_code"], 0)

        self.run_cli("stop")
        (self.state_dir / "sessions.json").write_text("{broken", encoding="utf-8")
        failed = self.run_cli(
            "--json", "doctor", "--host", "generic", "--scope", "project", "--port", "0",
            check=False, cwd=project,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(failed.stdout)["status"], "fail")

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_demo_export_and_replay_are_non_destructive(self):
        demo = json.loads(self.run_cli("--json", "demo", "--lang", "en", "--port", "0").stdout)
        self.assertIn("session=", demo["url"])
        source_id = demo["session_id"]
        export_file = Path(self.temp.name) / "history.json"
        self.run_cli("export", source_id, "--format", "events", "--file", str(export_file))
        exported = json.loads(export_file.read_text(encoding="utf-8"))
        self.assertEqual(exported["format"], "live-chat-export/v1")
        replayed = json.loads(
            self.run_cli("--json", "replay", "--file", str(export_file), "--speed", "0").stdout
        )
        self.assertNotEqual(replayed["session_id"], source_id)
        catalog = json.loads(self.run_cli("--json", "sessions", "list", "--archived").stdout)
        self.assertIn(source_id, {item["session_id"] for item in catalog["sessions"]})
        source = json.loads(self.run_cli("--json", "sessions", "show", source_id).stdout)
        self.assertEqual(len(source["state"]["messages"]), 3)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_invalid_replay_does_not_create_a_session(self):
        self.run_cli("start", "--port", "0", "--no-legacy")
        before = json.loads(self.run_cli("--json", "sessions", "list", "--archived").stdout)
        invalid = Path(self.temp.name) / "invalid.json"
        invalid.write_text(json.dumps({
            "format": "live-chat-export/v1",
            "kind": "events",
            "events": [{"type": "message.created", "payload": {"sender": "Agent", "text": ""}}],
        }), encoding="utf-8")
        result = self.run_cli("replay", "--file", str(invalid), check=False)
        self.assertNotEqual(result.returncode, 0)
        after = json.loads(self.run_cli("--json", "sessions", "list", "--archived").stdout)
        self.assertEqual(len(after["sessions"]), len(before["sessions"]))


if __name__ == "__main__":
    unittest.main()
