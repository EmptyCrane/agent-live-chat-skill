"""Command-line interface and server lifecycle management."""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
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
)
from .server import LiveChatHTTPServer
from .store import StateStore


class CliError(RuntimeError):
    pass


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


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
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
            message = value.get("error", {}).get("message", "%d %s" % (response.status, response.reason))
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
    store = StateStore(state_path(state_dir), legacy_path=legacy, logger=logger)
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
    _atomic_json(instance_path(state_dir), instance)
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
    text = (
        "[live-chat] 运行中: {url} pid={pid} messages={messages} "
        "participants={participants} typing={typing} session={session[status]} "
        "round={session[round][current]}/{session[round][max]}"
    ).format(**value)
    _emit(args, value, text)
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
