import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "skill" / "live-chat"
ENTRY = ROOT / "scripts" / "live_chat.py"
SKIP_PROCESS_TESTS = os.environ.get("LIVE_CHAT_SKIP_PROCESS_TESTS") == "1"
PROCESS_SKIP_REASON = (
    "hosted macOS runners currently close cross-process Python loopback listeners "
    "(actions/runner-images#14409)"
)


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

    def run_cli(self, *arguments, input_text=None, check=True):
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

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_full_lifecycle_and_stdin_message(self):
        started = self.run_cli("--json", "start", "--port", "0", "--no-legacy")
        instance = json.loads(started.stdout)
        self.assertTrue(instance["url"].startswith("http://127.0.0.1:"))
        self.assertEqual(instance["app_version"], "0.1.0-beta.4")
        self.run_cli("msg", "Alice", "--stdin", input_text="Line one\nLine two")
        self.run_cli("participants", "set", "Alice", "Waiting", "Alice")
        self.run_cli("session", "set", "--stdin", input_text=self.session_json())
        status = self.run_cli("--json", "status")
        value = json.loads(status.stdout)
        self.assertEqual(value["app_version"], "0.1.0-beta.4")
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


if __name__ == "__main__":
    unittest.main()
