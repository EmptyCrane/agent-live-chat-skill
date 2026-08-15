import contextlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def run_cli_with_cp936_bootstrap(self, *arguments, input_value=b"", check=True):
        """Send raw UTF-8 after Python initially configures its stdio as CP936."""
        environment = self.environment.copy()
        environment.pop("PYTHONUTF8", None)
        environment["PYTHONIOENCODING"] = "cp936"
        command = [
            sys.executable,
            "-B",
            str(ENTRY),
            "--state-dir",
            str(self.state_dir),
        ] + list(arguments)
        result = subprocess.run(
            command,
            input=input_value,
            capture_output=True,
            check=False,
            env=environment,
            timeout=15,
        )
        stdout = result.stdout.decode("utf-8", errors="strict")
        stderr = result.stderr.decode("utf-8", errors="strict")
        if check and result.returncode:
            self.fail(
                "CP936 bootstrap CLI failed (%s)\nstdout:\n%s\nstderr:\n%s"
                % (result.returncode, stdout, stderr)
            )
        return result, stdout, stderr

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

    def test_template_list_and_show_work_without_a_running_service(self):
        listed = self.run_cli("--json", "templates", "list", "--lang", "zh-CN")
        catalog = json.loads(listed.stdout)
        self.assertEqual(len(catalog["templates"]), 10)
        shown = self.run_cli(
            "--json", "templates", "show", "writers_room", "--lang", "zh-CN"
        )
        self.assertEqual(json.loads(shown.stdout)["template"]["name"], "编剧室")

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_full_lifecycle_and_stdin_message(self):
        started = self.run_cli("--json", "start", "--port", "0", "--no-legacy")
        instance = json.loads(started.stdout)
        self.assertTrue(instance["url"].startswith("http://127.0.0.1:"))
        self.assertEqual(instance["app_version"], "0.1.0-beta.11")
        self.run_cli("msg", "Alice", "--stdin", input_text="Line one\nLine two")
        self.run_cli("participants", "set", "Alice", "Waiting", "Alice")
        self.run_cli("session", "set", "--stdin", input_text=self.session_json())
        status = self.run_cli("--json", "status")
        value = json.loads(status.stdout)
        self.assertEqual(value["app_version"], "0.1.0-beta.11")
        self.assertTrue(value["active_session_id"])
        self.assertEqual(len(value["sessions"]), 1)
        self.assertEqual(value["messages"], 1)
        self.assertEqual(value["participants"], ["Alice", "Waiting"])
        self.assertEqual(value["session"]["status"], "running")
        self.assertEqual(value["session"]["round"]["current"], 1)
        self.assertEqual(value["typing"], [])
        expected_state = self.state_dir / "sessions" / value["active_session_id"] / "state.json"
        self.assertEqual(Path(value["state_file"]).resolve(), expected_state.resolve())
        self.assertTrue(expected_state.is_file())
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

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_utf8_stdio_overrides_cp936_for_structured_stdin_and_errors(self):
        self.run_cli("start", "--port", "0", "--no-legacy")
        proposal = {
            "background": "生产事故：订单服务出现乱码 🚨\n需要跨团队诊断",
            "objective": "找出根因并保留中文",
            "deliverable": "可执行的修复清单",
            "criteria": ["中文无变化", "emoji 保留 🧭"],
        }
        _, stdout, _ = self.run_cli_with_cp936_bootstrap(
            "--json",
            "templates",
            "apply",
            "incident_diagnosis",
            "--lang",
            "zh-CN",
            "--stdin",
            "--request-id",
            "9" * 32,
            input_value=json.dumps(proposal, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        applied = json.loads(stdout)
        self.assertEqual(applied["stage"], "plan_approval")
        status = json.loads(self.run_cli("--json", "status").stdout)
        self.assertEqual(status["session"]["background"], proposal["background"])
        decision_id = status["session"]["pending_decision"]["id"]

        response = "批准。保留多行说明：\n第一批先诊断；\n第二批再复核。✅"
        _, stdout, _ = self.run_cli_with_cp936_bootstrap(
            "--json",
            "decision",
            "resolve",
            decision_id,
            "approve",
            "--option-id",
            "approve",
            "--stdin",
            input_value=response.encode("utf-8"),
        )
        self.assertEqual(json.loads(stdout)["resolution"]["response"], response)

        event = {
            "type": "message.created",
            "source": {"host": "codex"},
            "payload": {"sender": "诊断智能体 🧪", "text": "日志显示：编码正常\n继续排查。🔎"},
        }
        _, stdout, _ = self.run_cli_with_cp936_bootstrap(
            "--json",
            "events",
            "emit",
            "--stdin",
            input_value=json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        emitted = json.loads(stdout)
        self.assertEqual(emitted["event"]["payload"], event["payload"])

        session_value = json.loads(self.session_json())
        session_value["roles"] = json.loads(self.run_cli("--json", "status").stdout)[
            "session"
        ]["roles"]
        session_value["background"] = "会话背景：中文与 emoji 🌐\n第二行"
        session_value["objective"] = "稳定往返所有字符"
        _, stdout, _ = self.run_cli_with_cp936_bootstrap(
            "--json",
            "session",
            "set",
            "--stdin",
            input_value=json.dumps(session_value, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        self.assertEqual(json.loads(stdout)["session"]["background"], session_value["background"])

        invalid, stdout, stderr = self.run_cli_with_cp936_bootstrap(
            "--json",
            "events",
            "emit",
            "--stdin",
            input_value="{\"文本\": \"未闭合 🚫\"".encode("utf-8"),
            check=False,
        )
        self.assertNotEqual(invalid.returncode, 0)
        error = json.loads(stdout)
        self.assertEqual(error["error"]["code"], "cli_error")
        self.assertIn("event input must be valid JSON", error["error"]["message"])
        self.assertEqual(stderr, "")
        self.assertNotIn("UnicodeDecodeError", stdout)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_concurrent_start_converges_and_stop_releases_log(self):
        for attempt in range(3):
            command = [
                sys.executable,
                "-B",
                str(ENTRY),
                "--state-dir",
                str(self.state_dir),
                "--json",
                "start",
                "--port",
                "0",
                "--no-legacy",
            ]
            processes = [
                subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self.environment,
                )
                for _ in range(2)
            ]
            results = [process.communicate(timeout=20) for process in processes]
            for process, (stdout, stderr) in zip(processes, results):
                self.assertEqual(process.returncode, 0, stderr.decode("utf-8"))
            instances = [json.loads(stdout.decode("utf-8")) for stdout, _ in results]
            self.assertEqual(
                {(item["instance_id"], item["pid"], item["url"]) for item in instances},
                {(instances[0]["instance_id"], instances[0]["pid"], instances[0]["url"])},
            )

            stopped = self.run_cli("stop")
            self.assertIn("服务已停止", stopped.stdout)
            self.assertFalse((self.state_dir / "instance.json").exists())
            log_path = self.state_dir / "server.log"
            moved_path = self.state_dir / ("server.released-%d.log" % attempt)
            log_path.rename(moved_path)
            moved_path.rename(log_path)

        duplicate = self.run_cli("stop", check=False)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("not running", duplicate.stderr)

    @unittest.skipUnless(sys.platform == "win32", "Windows log handles need repeated coverage")
    def test_windows_repeated_stop_immediately_releases_server_log(self):
        for index in range(3):
            self.run_cli("start", "--port", "0", "--no-legacy")
            self.run_cli("stop")
            log_path = self.state_dir / "server.log"
            moved_path = self.state_dir / ("server-%d.log" % index)
            log_path.rename(moved_path)
            moved_path.rename(log_path)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_doctor_reports_stale_and_malformed_instance_records_without_repair(self):
        self.state_dir.mkdir(parents=True)
        record_path = self.state_dir / "instance.json"
        malformed = "{malformed instance"
        record_path.write_text(malformed, encoding="utf-8")
        result = self.run_cli(
            "--json", "doctor", "--host", "generic", "--port", "0", check=False
        )
        check = next(
            item for item in json.loads(result.stdout)["checks"]
            if item["id"] == "instance_record"
        )
        self.assertEqual(check["status"], "warn")
        self.assertEqual(record_path.read_text(encoding="utf-8"), malformed)

        stale = {
            "instance_id": "stale-instance",
            "pid": 2147483647,
            "url": "http://127.0.0.1:1",
        }
        record_path.write_text(json.dumps(stale), encoding="utf-8")
        result = self.run_cli(
            "--json", "doctor", "--host", "generic", "--port", "0", check=False
        )
        check = next(
            item for item in json.loads(result.stdout)["checks"]
            if item["id"] == "instance_record"
        )
        self.assertEqual(check["status"], "warn")
        self.assertIn("stale", check["detail"])
        self.assertEqual(json.loads(record_path.read_text(encoding="utf-8")), stale)

        started = json.loads(
            self.run_cli("--json", "start", "--port", "0", "--no-legacy").stdout
        )
        replacement = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(replacement["instance_id"], started["instance_id"])
        self.assertNotEqual(replacement["instance_id"], stale["instance_id"])

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_external_port_occupancy_fails_without_disturbing_owner(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
            owner.bind(("127.0.0.1", 0))
            owner.listen(1)
            port = owner.getsockname()[1]
            failed = self.run_cli(
                "start", "--port", str(port), "--no-legacy", check=False
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("already in use", failed.stderr)
            self.assertFalse((self.state_dir / "instance.json").exists())
            self.assertEqual(owner.getsockname()[1], port)

        started = json.loads(
            self.run_cli("--json", "start", "--port", str(port), "--no-legacy").stdout
        )
        self.assertEqual(started["port"], port)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_startup_failure_does_not_overwrite_corrupt_state(self):
        self.state_dir.mkdir(parents=True)
        state_path = self.state_dir / "sessions.json"
        corrupt = "{corrupt session catalog 🚫"
        state_path.write_text(corrupt, encoding="utf-8")
        failed = self.run_cli(
            "--json", "start", "--port", "0", "--no-legacy", check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("service exited during startup", json.loads(failed.stdout)["error"]["message"])
        self.assertEqual(state_path.read_text(encoding="utf-8"), corrupt)
        self.assertFalse((self.state_dir / "instance.json").exists())

        doctor = self.run_cli(
            "--json", "doctor", "--host", "generic", "--port", "0", check=False
        )
        self.assertEqual(doctor.returncode, 1)
        state_check = next(
            item for item in json.loads(doctor.stdout)["checks"] if item["id"] == "state"
        )
        self.assertEqual(state_check["status"], "fail")
        self.assertEqual(state_path.read_text(encoding="utf-8"), corrupt)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_service_crash_leaves_diagnostic_record_and_start_replaces_it(self):
        started = json.loads(
            self.run_cli("--json", "start", "--port", "0", "--no-legacy").stdout
        )
        os.kill(started["pid"], signal.SIGTERM)
        self.assertTrue(cli._wait_for_process_exit(started["pid"], 5))
        record_path = self.state_dir / "instance.json"
        self.assertEqual(
            json.loads(record_path.read_text(encoding="utf-8"))["instance_id"],
            started["instance_id"],
        )

        doctor = self.run_cli(
            "--json", "doctor", "--host", "generic", "--port", "0", check=False
        )
        record_check = next(
            item for item in json.loads(doctor.stdout)["checks"]
            if item["id"] == "instance_record"
        )
        self.assertEqual(record_check["status"], "warn")

        restarted = json.loads(
            self.run_cli("--json", "start", "--port", "0", "--no-legacy").stdout
        )
        self.assertNotEqual(restarted["instance_id"], started["instance_id"])
        self.assertEqual(
            json.loads(record_path.read_text(encoding="utf-8"))["instance_id"],
            restarted["instance_id"],
        )

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_live_recorded_pid_with_mismatched_health_fails_closed(self):
        self.state_dir.mkdir(parents=True)
        owned = subprocess.Popen(
            [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
            env=self.environment,
        )
        record = {
            "instance_id": "mismatched-live-process",
            "pid": owned.pid,
            "url": "http://127.0.0.1:1",
        }
        try:
            (self.state_dir / "instance.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            failed = self.run_cli(
                "--json", "start", "--port", "0", "--no-legacy", check=False
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("live process but health does not match", failed.stdout)
            self.assertIsNone(owned.poll())
            self.assertEqual(
                json.loads((self.state_dir / "instance.json").read_text(encoding="utf-8")),
                record,
            )
        finally:
            owned.terminate()
            owned.wait(timeout=5)

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_startup_timeout_terminates_the_owned_real_child(self):
        original_popen = subprocess.Popen
        children = []

        def sleeping_child(*_args, **_kwargs):
            child = original_popen(
                [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                env=self.environment,
            )
            children.append(child)
            return child

        args = SimpleNamespace(
            state_dir=str(self.state_dir),
            no_legacy=True,
            port=0,
            json_output=True,
        )
        with patch.object(cli.subprocess, "Popen", side_effect=sleeping_child), patch.object(
            cli, "_instance_health", return_value=(None, None)
        ), patch.object(cli.time, "time", side_effect=(0, 9)):
            with self.assertRaises(cli.CliError) as caught:
                cli._start_locked(args, self.state_dir)
        self.assertIn("startup timed out", str(caught.exception))
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

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
        template_check = next(check for check in value["checks"] if check["id"] == "template_catalog")
        self.assertEqual(template_check["status"], "pass")
        self.assertIn("10 validated", template_check["detail"])

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
        self.assertEqual(exported["format"], "live-chat-export/v2")
        replayed = json.loads(
            self.run_cli("--json", "replay", "--file", str(export_file), "--speed", "0").stdout
        )
        self.assertNotEqual(replayed["session_id"], source_id)
        catalog = json.loads(self.run_cli("--json", "sessions", "list", "--archived").stdout)
        self.assertIn(source_id, {item["session_id"] for item in catalog["sessions"]})
        source = json.loads(self.run_cli("--json", "sessions", "show", source_id).stdout)
        self.assertEqual(len(source["state"]["messages"]), 3)

        exported["format"] = "live-chat-export/v1"
        for event in exported["events"]:
            event["event_version"] = 1
        v1_export = Path(self.temp.name) / "history-v1.json"
        v1_export.write_text(json.dumps(exported), encoding="utf-8")
        replayed_v1 = json.loads(
            self.run_cli("--json", "replay", "--file", str(v1_export), "--speed", "0").stdout
        )
        self.assertNotIn(replayed_v1["session_id"], {source_id, replayed["session_id"]})

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_decision_request_resolve_and_duplicate_retry(self):
        self.run_cli("start", "--port", "0", "--no-legacy")
        self.run_cli("participants", "set", "Alice", "Waiting")
        request_value = {
            "id": "d" * 32,
            "kind": "plan_approval",
            "prompt": "Approve the session?",
            "options": [
                {"id": "approve", "label": "Approve"},
                {"id": "edit", "label": "Edit"},
            ],
            "session": json.loads(self.session_json()),
        }
        requested = self.run_cli(
            "--json", "decision", "request", "--stdin",
            input_text=json.dumps(request_value),
        )
        self.assertEqual(json.loads(requested.stdout)["decision"]["id"], "d" * 32)
        resolved = self.run_cli(
            "--json", "decision", "resolve", "d" * 32, "approve",
            "--option-id", "approve",
        )
        self.assertEqual(json.loads(resolved.stdout)["resolution"]["action"], "approve")
        duplicate = self.run_cli(
            "--json", "decision", "resolve", "d" * 32, "approve",
            "--option-id", "approve",
        )
        self.assertTrue(json.loads(duplicate.stdout)["duplicate"])

    @unittest.skipIf(SKIP_PROCESS_TESTS, PROCESS_SKIP_REASON)
    def test_template_apply_export_and_replay_preserve_metadata(self):
        self.run_cli("start", "--port", "0", "--no-legacy")
        payload = {
            "background": "CLI template test",
            "objective": "Review the design",
            "deliverable": "A recommendation",
            "criteria": ["Risks are explicit"],
        }
        applied = json.loads(self.run_cli(
            "--json", "templates", "apply", "architecture_review",
            "--lang", "en", "--stdin", "--request-id", "7" * 32,
            "--host", "codex", input_text=json.dumps(payload),
        ).stdout)
        self.assertEqual(applied["stage"], "plan_approval")
        status = json.loads(self.run_cli("--json", "status").stdout)
        session_id = status["active_session_id"]
        self.assertEqual(status["session"]["workflow"]["template"]["id"], "architecture_review")
        export_path = Path(self.temp.name) / "template-export.json"
        self.run_cli("export", session_id, "--format", "snapshot", "--file", str(export_path))
        replayed = json.loads(self.run_cli(
            "--json", "replay", "--file", str(export_path), "--speed", "0"
        ).stdout)
        replay_state = json.loads(
            self.run_cli("--json", "sessions", "show", replayed["session_id"]).stdout
        )["state"]
        self.assertEqual(
            replay_state["session"]["workflow"]["template"]["id"],
            "architecture_review",
        )

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
