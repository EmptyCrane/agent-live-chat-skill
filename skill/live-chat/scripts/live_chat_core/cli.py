"""Command-line interface and server lifecycle management."""

import argparse
import contextlib
import io
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import urlsplit

from .config import (
    APP_VERSION,
    DEFAULT_HOST,
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    SERVICE_NAME,
    chat_asset_path,
    default_state_dir,
    instance_path,
    legacy_messages_path,
    log_path,
    migrate_legacy_state,
    state_path,
    sessions_dir,
)
from .adapters import HOST_ADAPTERS, public_adapter
from .io_utils import atomic_json
from .server import LiveChatHTTPServer
from .sessions import MAX_EVENTS, SessionStore, validate_event_input
from .validation import validate_seed


class CliError(RuntimeError):
    pass


def _http_error_message(value, status, reason):
    """Return a stable message for structured and legacy HTTP errors."""
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        elif isinstance(error, str) and error:
            return error
    return "%d %s" % (status, reason)


def _read_instance(state_dir):
    path = instance_path(state_dir)
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            return None
        return value
    except (OSError, ValueError):
        return None


def _request_json(url, method="GET", payload=None, timeout=3):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise CliError("live-chat URL must use HTTP on a loopback host")
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        # ASCII JSON escaping preserves normal Unicode and safely transports
        # surrogate-escaped terminal input to the server's normalization layer.
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    connection = HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    try:
        connection.request(method, target, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        value = json.loads(raw.decode("utf-8"))
        if response.status >= 400:
            message = _http_error_message(value, response.status, response.reason)
            raise CliError("server rejected the request: %s" % message)
        return value
    except CliError:
        raise
    except (HTTPException, OSError, UnicodeDecodeError, ValueError) as exc:
        raise CliError("cannot reach live-chat service: %s" % exc) from exc
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _health(url, timeout=1):
    try:
        value = _request_json(url.rstrip("/") + "/api/health", timeout=timeout)
    except CliError:
        return None
    if value.get("service") != SERVICE_NAME or value.get("protocol_version") != PROTOCOL_VERSION:
        return None
    return value


def _instance_health(state_dir):
    instance = _read_instance(state_dir)
    if not instance:
        return None, None
    health = _health(str(instance.get("url", "")))
    if not health or health.get("instance_id") != instance.get("instance_id"):
        return instance, None
    return instance, health


def _port_available(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _state_dir(args):
    return Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir()


def _resolve_url(args):
    if args.url:
        return args.url.rstrip("/")
    environment_url = os.environ.get("LIVE_CHAT_URL")
    if environment_url:
        return environment_url.rstrip("/")
    state_dir = _state_dir(args)
    instance, health = _instance_health(state_dir)
    if health:
        return str(instance["url"]).rstrip("/")
    fallback = "http://%s:%d" % (DEFAULT_HOST, DEFAULT_PORT)
    if _health(fallback):
        return fallback
    raise CliError("live-chat service is not running; run the start command first")


def _emit(args, value, text):
    if args.json_output:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    else:
        print(text)


def _serve(args):
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("live-chat")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    handler = logging.FileHandler(str(log_path(state_dir)), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    instance_id = uuid.uuid4().hex
    legacy = Path(args.legacy_path) if args.legacy_path else None
    store = SessionStore(state_dir, legacy_path=legacy, logger=logger)
    server = LiveChatHTTPServer(
        (DEFAULT_HOST, args.port),
        store=store,
        instance_id=instance_id,
        asset_path=chat_asset_path(),
        logger=logger,
    )
    actual_port = server.server_address[1]
    instance = {
        "app_version": APP_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "instance_id": instance_id,
        "pid": os.getpid(),
        "host": DEFAULT_HOST,
        "port": actual_port,
        "url": "http://%s:%d" % (DEFAULT_HOST, actual_port),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(instance_path(state_dir), instance)
    logger.info("service started instance=%s url=%s", instance_id, instance["url"])
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        current = _read_instance(state_dir)
        if current and current.get("instance_id") == instance_id:
            try:
                instance_path(state_dir).unlink()
            except OSError:
                logger.warning("could not remove instance file")
        logger.info("service stopped instance=%s", instance_id)
        handler.close()
    return 0


def _start(args):
    state_dir = _state_dir(args)
    if not args.state_dir and not os.environ.get("LIVE_CHAT_STATE_DIR"):
        migrate_legacy_state(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    instance, health = _instance_health(state_dir)
    if health:
        _emit(args, instance, "[live-chat] 服务已在运行: %s" % instance["url"])
        return 0

    requested_port = args.port
    if requested_port < 0 or requested_port > 65535:
        raise CliError("port must be between 0 and 65535")
    if requested_port == DEFAULT_PORT and not _port_available(DEFAULT_HOST, requested_port):
        requested_port = 0
    elif requested_port != 0 and not _port_available(DEFAULT_HOST, requested_port):
        raise CliError("requested port %d is already in use" % requested_port)

    entrypoint = Path(__file__).resolve().parents[1] / "live_chat.py"
    legacy = "" if args.no_legacy else str(legacy_messages_path())
    command = [
        sys.executable,
        str(entrypoint),
        "--state-dir",
        str(state_dir),
        "_serve",
        "--port",
        str(requested_port),
        "--legacy-path",
        legacy,
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    previous_id = instance.get("instance_id") if instance else None
    with log_path(state_dir).open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(entrypoint.parent),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    deadline = time.time() + 8
    while time.time() < deadline:
        if process.poll() is not None:
            raise CliError("service exited during startup; inspect %s" % log_path(state_dir))
        current, current_health = _instance_health(state_dir)
        if current_health and current.get("instance_id") != previous_id:
            _emit(args, current, "[live-chat] 服务已启动: %s (pid=%s)" % (current["url"], current["pid"]))
            return 0
        time.sleep(0.2)
    raise CliError("service startup timed out; inspect %s" % log_path(state_dir))


def _status(args):
    state_dir = _state_dir(args)
    instance, health = _instance_health(state_dir)
    if not health:
        raise CliError("live-chat service is not running")
    state = _request_json(instance["url"] + "/api/state?since=0")
    value = {
        "ok": True,
        "app_version": APP_VERSION,
        "url": instance["url"],
        "pid": health["pid"],
        "instance_id": health["instance_id"],
        "started_at": instance.get("started_at"),
        "epoch": state["epoch"],
        "revision": state["revision"],
        "messages": state["total"],
        "participants": state.get("participants", []),
        "typing": list(state["typing"].keys()),
        "session": state.get("session", {}),
        "state_file": str(state_path(state_dir)),
        "log_file": str(log_path(state_dir)),
    }
    if "sessions" in health.get("features", []):
        catalog = _request_json(instance["url"] + "/api/sessions?include_archived=1")
        value["active_session_id"] = catalog["active_session_id"]
        value["sessions"] = catalog["sessions"]
        value["state_file"] = str(
            sessions_dir(state_dir) / catalog["active_session_id"] / "state.json"
        )
    text = (
        "[live-chat] 运行中: {url} pid={pid} messages={messages} "
        "participants={participants} typing={typing} session={session[status]} "
        "round={session[round][current]}/{session[round][max]}"
    ).format(**value)
    _emit(args, value, text)
    return 0


def _feature_url(args, feature):
    url = _resolve_url(args)
    health = _request_json(url.rstrip("/") + "/api/health")
    if (
        health.get("service") != SERVICE_NAME
        or health.get("protocol_version") != PROTOCOL_VERSION
        or feature not in health.get("features", [])
    ):
        raise CliError("unsupported_feature: the running service does not support %s" % feature)
    return url


def _doctor_check(checks, check_id, status, detail, remediation=""):
    checks.append({
        "id": check_id,
        "status": status,
        "detail": detail,
        "remediation": remediation,
    })


def _doctor(args):
    state_dir = _state_dir(args)
    checks = []
    version_ok = sys.version_info >= (3, 9)
    _doctor_check(
        checks,
        "python",
        "pass" if version_ok else "fail",
        "%d.%d.%d" % sys.version_info[:3],
        "Install Python 3.9 or newer." if not version_ok else "",
    )
    required = [
        chat_asset_path(),
        Path(__file__).resolve().parents[1] / "live_chat.py",
        Path(__file__).resolve().parents[2] / "SKILL.md",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    _doctor_check(
        checks,
        "skill_files",
        "fail" if missing else "pass",
        "missing: %s" % ", ".join(missing) if missing else "required runtime files are present",
        "Reinstall the Skill from a verified archive." if missing else "",
    )
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / (".doctor-%s.tmp" % uuid.uuid4().hex)
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("ok")
        probe.unlink()
        _doctor_check(checks, "state_directory", "pass", str(state_dir))
    except OSError as exc:
        _doctor_check(checks, "state_directory", "fail", str(exc), "Choose a writable --state-dir.")
    snapshot_files = [state_dir / "sessions.json", state_dir / "state.json"]
    existing = [path for path in snapshot_files if path.exists()]
    if existing:
        try:
            if (state_dir / "sessions.json").exists():
                SessionStore(state_dir).validate_all()
            else:
                with (state_dir / "state.json").open("r", encoding="utf-8") as handle:
                    json.load(handle)
            _doctor_check(checks, "state", "pass", "state metadata is readable")
        except (OSError, ValueError, RuntimeError) as exc:
            _doctor_check(checks, "state", "fail", str(exc), "Back up the state directory before recovery.")
    else:
        _doctor_check(checks, "state", "warn", "no state snapshot exists yet")
    instance, health = _instance_health(state_dir)
    if health:
        _doctor_check(
            checks,
            "service",
            "pass",
            "%s protocol=%s" % (instance["url"], health["protocol_version"]),
        )
    elif _port_available(DEFAULT_HOST, args.port):
        _doctor_check(checks, "service", "warn", "service is stopped and port %d is available" % args.port)
    else:
        candidate = _health("http://%s:%d" % (DEFAULT_HOST, args.port))
        _doctor_check(
            checks,
            "service",
            "warn" if candidate else "fail",
            "port %d is used by %s" % (args.port, "live-chat" if candidate else "another process"),
            "Choose another port." if not candidate else "",
        )
    try:
        descriptor = public_adapter(
            args.host,
            args.scope,
            Path.home(),
            Path.cwd(),
            os.environ,
        )
        root = Path(descriptor["resolved_root"])
        installed = (root / "live-chat" / "SKILL.md").is_file()
        _doctor_check(
            checks,
            "installation",
            "pass" if installed else "warn",
            ("installed at " if installed else "not installed at ") + str(root / "live-chat"),
        )
        if args.host == "codex":
            agents_copy = Path.home() / ".agents" / "skills" / "live-chat" / "SKILL.md"
            codex_copy = root / "live-chat" / "SKILL.md"
            duplicate = agents_copy.is_file() and codex_copy.is_file() and agents_copy.resolve() != codex_copy.resolve()
            _doctor_check(
                checks,
                "duplicate_skill",
                "warn" if duplicate else "pass",
                "both Codex and .agents copies exist" if duplicate else "no cross-root duplicate detected",
                "Disable one path in Codex config if both are discovered." if duplicate else "",
            )
    except (OSError, ValueError) as exc:
        _doctor_check(checks, "installation", "fail", str(exc))
    failed = [check for check in checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    exit_code = 1 if failed else (2 if warned else 0)
    value = {
        "ok": not failed,
        "status": "fail" if failed else ("warn" if warned else "pass"),
        "exit_code": exit_code,
        "checks": checks,
    }
    if args.json_output:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    else:
        for check in checks:
            print("[%s] %s: %s" % (check["status"].upper(), check["id"], check["detail"]))
    return exit_code


def _sessions(args):
    url = _feature_url(args, "sessions")
    if args.action == "list":
        suffix = "?include_archived=1" if args.archived else ""
        value = _request_json(url + "/api/sessions" + suffix)
    elif args.action == "show":
        catalog = _request_json(url + "/api/sessions?include_archived=1")
        match = next((entry for entry in catalog["sessions"] if entry["session_id"] == args.session_id), None)
        if not match:
            raise CliError("session does not exist")
        state = _request_json(url + "/api/state?since=0&session=" + args.session_id)
        value = {"session": match, "state": state}
    elif args.action == "create":
        value = _request_json(
            url + "/api/sessions",
            method="POST",
            payload={"title": args.title, "subtitle": args.subtitle, "source": {"host": args.host}},
        )
    else:
        value = _request_json(
            url + "/api/sessions/" + args.action,
            method="POST",
            payload={"session_id": args.session_id, "source": {"host": args.host}},
        )
    _emit(args, value, json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def _export(args):
    url = _feature_url(args, "export")
    session_id = args.session_id
    if not session_id:
        session_id = _request_json(url + "/api/sessions")["active_session_id"]
    if args.format == "snapshot":
        state = _request_json(url + "/api/state?since=0&session=" + session_id)
        for field in ("protocol_version", "instance_id"):
            state.pop(field, None)
        payload = {"format": "live-chat-export/v1", "kind": "snapshot", "session_id": session_id, "state": state}
    else:
        history = _request_json(url + "/api/events?after=0&session=" + session_id)
        payload = {"format": "live-chat-export/v1", "kind": "events", "session_id": session_id, "events": history["events"]}
    output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.file:
        try:
            with Path(args.file).open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(output)
        except OSError as exc:
            raise CliError("cannot write export: %s" % exc) from exc
        _emit(args, {"ok": True, "file": str(Path(args.file).resolve())}, "[live-chat] 已导出: %s" % args.file)
    else:
        print(output, end="")
    return 0


def _read_export(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CliError("cannot read replay export: %s" % exc) from exc
    if not isinstance(value, dict) or value.get("format") != "live-chat-export/v1":
        raise CliError("unsupported replay export format")
    return value


def _replay(args):
    if args.speed < 0:
        raise CliError("speed must be zero or greater")
    url = _feature_url(args, "replay")
    value = _read_export(args.file)
    kind = value.get("kind")
    prepared_seed = None
    prepared_events = None
    if kind == "snapshot":
        state = value.get("state")
        if not isinstance(state, dict):
            raise CliError("snapshot replay must contain a state object")
        prepared_seed = {field: state.get(field) for field in ("scene", "session", "participants", "messages")}
        try:
            validate_seed(prepared_seed)
        except ValueError as exc:
            raise CliError("invalid snapshot replay: %s" % exc) from exc
    elif kind == "events":
        raw_events = value.get("events")
        if not isinstance(raw_events, list) or not raw_events or len(raw_events) > MAX_EVENTS:
            raise CliError("event replay must contain 1-%d events" % MAX_EVENTS)
        try:
            prepared_events = [validate_event_input(event) for event in raw_events]
            # Validate the full sequence against a disposable state before the
            # destination service creates a replay session.  Envelope checks
            # alone cannot catch payload errors such as an empty message.
            with tempfile.TemporaryDirectory() as temporary:
                SessionStore(Path(temporary)).emit_batch(prepared_events)
        except ValueError as exc:
            raise CliError("invalid event replay: %s" % exc) from exc
    else:
        raise CliError("replay export kind must be snapshot or events")
    created = _request_json(
        url + "/api/sessions",
        method="POST",
        payload={"title": args.title or "Replay", "subtitle": "Imported history", "source": {"host": "manual"}},
    )
    session_id = created["session"]["session_id"]
    if kind == "snapshot":
        result = _request_json(url + "/api/events", method="POST", payload={
            "session_id": session_id,
            "type": "conversation.seeded",
            "source": {"host": "manual"},
            "payload": prepared_seed,
        })
    else:
        events = []
        previous_time = None
        for exported, normalized in zip(value["events"], prepared_events):
            source = dict(normalized["source"])
            source["replay_of"] = str(exported.get("event_id", ""))[:64]
            replayed = {
                "type": normalized["type"],
                "occurred_at": normalized.get("occurred_at"),
                "source": source,
                "payload": normalized["payload"],
            }
            if args.speed > 0:
                current_time = exported.get("occurred_at")
                if previous_time and current_time:
                    try:
                        before = datetime.fromisoformat(previous_time.replace("Z", "+00:00"))
                        after = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
                        time.sleep(max(0, min((after - before).total_seconds() / args.speed, 5)))
                    except ValueError:
                        pass
                response = _request_json(
                    url + "/api/events",
                    method="POST",
                    payload=dict(replayed, session_id=session_id),
                    timeout=8,
                )
                previous_time = current_time
            else:
                events.append(replayed)
        result = response if args.speed > 0 else _request_json(
            url + "/api/events/batch",
            method="POST",
            payload={"session_id": session_id, "events": events},
            timeout=30,
        )
    output = {"ok": True, "session_id": session_id, "url": url + "/?session=" + session_id, "result": result}
    _emit(args, output, "[live-chat] 回放会话已创建: %s" % output["url"])
    return 0


def _events(args):
    url = _feature_url(args, "events")
    try:
        value = json.load(sys.stdin)
    except ValueError as exc:
        raise CliError("event input must be valid JSON: %s" % exc) from exc
    if not isinstance(value, dict):
        raise CliError("event input must be an object")
    result = _request_json(url + "/api/events", method="POST", payload=value, timeout=8)
    _emit(args, result, "[live-chat] 事件已提交")
    return 0


def _adapter(args):
    value = public_adapter(args.host, args.scope, Path.home(), Path.cwd(), os.environ)
    _emit(args, value, json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def _demo(args):
    quiet = argparse.Namespace(**vars(args))
    quiet.json_output = True
    quiet.no_legacy = True
    with contextlib.redirect_stdout(io.StringIO()):
        _start(quiet)
    url = _feature_url(args, "demo")
    is_zh = args.lang == "zh-CN"
    created = _request_json(url + "/api/sessions", method="POST", payload={
        "title": "多智能体方案评审" if is_zh else "Multi-agent design review",
        "subtitle": "Live Chat Demo",
        "source": {"host": args.host},
    })
    session_id = created["session"]["session_id"]
    names = ["架构师", "审查员", "执行者"] if is_zh else ["Architect", "Critic", "Operator"]
    messages = [
        {"sender": names[0], "text": "先明确状态边界和兼容目标。" if is_zh else "Start with state boundaries and compatibility goals."},
        {"sender": names[1], "text": "迁移必须非破坏，并覆盖失败恢复。" if is_zh else "Migration must be non-destructive and cover failure recovery."},
        {"sender": names[2], "text": "用隔离会话完成端到端验证。" if is_zh else "Validate end to end in an isolated session."},
    ]
    _request_json(url + "/api/events", method="POST", payload={
        "session_id": session_id,
        "type": "conversation.seeded",
        "source": {"host": args.host},
        "payload": {
            "scene": {"title": created["session"]["title"], "subtitle": "Live Chat Demo"},
            "participants": names,
            "messages": messages,
        },
    })
    demo_url = url + "/?session=" + session_id + "&lang=" + args.lang
    value = {"ok": True, "url": demo_url, "session_id": session_id}
    _emit(args, value, "[live-chat] Demo 已就绪: %s" % demo_url)
    return 0


def _post(args, path, payload, success_text):
    result = _request_json(_resolve_url(args) + path, method="POST", payload=payload, timeout=8)
    _emit(args, result, success_text)
    return 0


def _message_text(args, remaining):
    sources = int(args.stdin) + int(bool(args.file)) + int(bool(remaining))
    if sources != 1:
        raise CliError("provide message text using positional arguments, --stdin, or --file")
    if args.stdin:
        return sys.stdin.read()
    if args.file:
        try:
            return Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError("cannot read message file: %s" % exc) from exc
    return " ".join(remaining)


def _msg(args):
    parts = list(args.parts)
    if args.system:
        sender = ""
        remaining = parts
    else:
        if not parts:
            raise CliError("a sender is required for a non-system message")
        sender = parts.pop(0)
        remaining = parts
    text = _message_text(args, remaining)
    return _post(args, "/api/msg", {"sender": sender, "text": text, "sys": args.system}, "[live-chat] 消息已推送")


def _typing(args):
    if args.clear:
        if args.sender or args.active:
            raise CliError("--clear cannot be combined with sender or state")
        payload = {"clear": True}
    else:
        if not args.sender or args.active not in ("on", "off"):
            raise CliError("typing requires <sender> on|off, or --clear")
        payload = {"sender": args.sender, "active": args.active == "on"}
    return _post(args, "/api/typing", payload, "[live-chat] 输入状态已更新")


def _participants(args):
    if args.action == "set":
        if not args.names:
            raise CliError("participants set requires at least one name")
        names = args.names
    else:
        if args.names:
            raise CliError("participants clear does not accept names")
        names = []
    return _post(
        args,
        "/api/participants",
        {"participants": names},
        "[live-chat] 参与者名册已更新",
    )


def _session(args):
    if args.action == "clear":
        if args.file or args.stdin:
            raise CliError("session clear does not accept --file or --stdin")
        payload = {"session": None}
    else:
        if bool(args.file) == bool(args.stdin):
            raise CliError("session set requires exactly one of --file or --stdin")
        try:
            text = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
            session = json.loads(text)
        except (OSError, ValueError) as exc:
            raise CliError("cannot read session JSON: %s" % exc) from exc
        payload = {"session": session}
    return _post(args, "/api/session", payload, "[live-chat] 会话计划已更新")


def _scene(args):
    return _post(
        args,
        "/api/scene",
        {"scene": {"title": args.title, "subtitle": " ".join(args.subtitle)}},
        "[live-chat] 话题已更新",
    )


def _reset(args):
    scene = None
    if args.title is not None:
        scene = {"title": args.title, "subtitle": " ".join(args.subtitle)}
    return _post(args, "/api/reset", {"scene": scene}, "[live-chat] 已清空并开始新场次")


def _seed(args):
    if bool(args.file) == bool(args.stdin):
        raise CliError("seed requires exactly one of --file or --stdin")
    try:
        text = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, ValueError) as exc:
        raise CliError("cannot read seed JSON: %s" % exc) from exc
    return _post(args, "/api/seed", payload, "[live-chat] 历史对话已导入")


def _stop(args):
    state_dir = _state_dir(args)
    instance, health = _instance_health(state_dir)
    url = args.url or os.environ.get("LIVE_CHAT_URL")
    if not health and not url:
        raise CliError("live-chat service is not running")
    target = url.rstrip("/") if url else instance["url"]
    result = _request_json(target + "/api/shutdown", method="POST", payload={})
    deadline = time.time() + 5
    while time.time() < deadline and _health(target, timeout=0.3):
        time.sleep(0.1)
    _emit(args, result, "[live-chat] 服务已停止")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Run and control the local live-chat service")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    parser.add_argument("--state-dir", help="override runtime state directory")
    parser.add_argument("--url", help="override service URL")
    parser.add_argument("--json", dest="json_output", action="store_true", help="emit JSON output")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="start or reuse the service")
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument("--no-legacy", action="store_true", help="do not import legacy messages.jsonl")
    start.set_defaults(handler=_start)

    status = commands.add_parser("status", help="show service state")
    status.set_defaults(handler=_status)

    doctor = commands.add_parser("doctor", help="diagnose runtime, state, and installation")
    doctor.add_argument("--host", choices=tuple(HOST_ADAPTERS), default="codex")
    doctor.add_argument("--scope", choices=("user", "project"), default="user")
    doctor.add_argument("--port", type=int, default=DEFAULT_PORT)
    doctor.set_defaults(handler=_doctor)

    demo = commands.add_parser("demo", help="start a deterministic non-destructive demo")
    demo.add_argument("--lang", choices=("en", "zh-CN"), default="en")
    demo.add_argument("--port", type=int, default=DEFAULT_PORT)
    demo.add_argument("--host", choices=tuple(HOST_ADAPTERS), default="codex")
    demo.set_defaults(handler=_demo)

    msg = commands.add_parser("msg", help="push a message")
    msg.add_argument("--sys", dest="system", action="store_true")
    msg.add_argument("--stdin", action="store_true")
    msg.add_argument("--file")
    msg.add_argument("parts", nargs="*")
    msg.set_defaults(handler=_msg)

    typing = commands.add_parser("typing", help="update typing state")
    typing.add_argument("sender", nargs="?")
    typing.add_argument("active", nargs="?")
    typing.add_argument("--clear", action="store_true")
    typing.set_defaults(handler=_typing)

    participants = commands.add_parser("participants", help="replace or clear participant roster")
    participants.add_argument("action", choices=("set", "clear"))
    participants.add_argument("names", nargs="*")
    participants.set_defaults(handler=_participants)

    session = commands.add_parser("session", help="replace or clear the session plan")
    session.add_argument("action", choices=("set", "clear"))
    session.add_argument("--file")
    session.add_argument("--stdin", action="store_true")
    session.set_defaults(handler=_session)

    scene = commands.add_parser("scene", help="update scene without clearing messages")
    scene.add_argument("title")
    scene.add_argument("subtitle", nargs="*")
    scene.set_defaults(handler=_scene)

    reset = commands.add_parser("reset", help="clear messages and optionally update scene")
    reset.add_argument("title", nargs="?")
    reset.add_argument("subtitle", nargs="*")
    reset.set_defaults(handler=_reset)

    seed = commands.add_parser("seed", help="replace the conversation from JSON")
    seed.add_argument("--file")
    seed.add_argument("--stdin", action="store_true")
    seed.set_defaults(handler=_seed)

    sessions = commands.add_parser("sessions", help="manage persistent conversations")
    session_commands = sessions.add_subparsers(dest="action", required=True)
    sessions_list = session_commands.add_parser("list", help="list conversations")
    sessions_list.add_argument("--archived", action="store_true", help="include archived sessions")
    sessions_list.set_defaults(handler=_sessions)
    sessions_show = session_commands.add_parser("show", help="show one conversation")
    sessions_show.add_argument("session_id")
    sessions_show.set_defaults(handler=_sessions)
    sessions_create = session_commands.add_parser("create", help="create and select a conversation")
    sessions_create.add_argument("--title")
    sessions_create.add_argument("--subtitle", default="")
    sessions_create.add_argument("--host", choices=tuple(HOST_ADAPTERS) + ("manual",), default="manual")
    sessions_create.set_defaults(handler=_sessions)
    for action in ("select", "archive", "restore"):
        session_action = session_commands.add_parser(action, help="%s a conversation" % action)
        session_action.add_argument("session_id")
        session_action.add_argument("--host", choices=tuple(HOST_ADAPTERS) + ("manual",), default="manual")
        session_action.set_defaults(handler=_sessions)

    export = commands.add_parser("export", help="export a snapshot or event history")
    export.add_argument("session_id", nargs="?")
    export.add_argument("--format", choices=("snapshot", "events"), default="snapshot")
    export.add_argument("--file")
    export.set_defaults(handler=_export)

    replay = commands.add_parser("replay", help="replay an exported conversation into a new session")
    replay.add_argument("--file", required=True)
    replay.add_argument("--speed", type=float, default=0)
    replay.add_argument("--title")
    replay.set_defaults(handler=_replay)

    events = commands.add_parser("events", help="submit a normalized event")
    events.add_argument("action", choices=("emit",))
    events.add_argument("--stdin", action="store_true", required=True)
    events.set_defaults(handler=_events)

    adapter = commands.add_parser("adapter", help="show host adapter metadata")
    adapter.add_argument("action", choices=("show",))
    adapter.add_argument("host", choices=tuple(HOST_ADAPTERS))
    adapter.add_argument("--scope", choices=("user", "project"), default="user")
    adapter.set_defaults(handler=_adapter)

    stop = commands.add_parser("stop", help="stop the matching service instance")
    stop.set_defaults(handler=_stop)

    serve = commands.add_parser("_serve", help=argparse.SUPPRESS)
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--legacy-path", default="")
    serve.set_defaults(handler=_serve)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (CliError, RuntimeError, ValueError) as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"error": {"code": "cli_error", "message": str(exc)}}, ensure_ascii=False))
        else:
            print("[live-chat] 错误: %s" % exc, file=sys.stderr)
        return 1
