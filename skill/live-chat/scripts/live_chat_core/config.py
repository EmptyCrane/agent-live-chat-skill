"""Configuration and path discovery for live-chat."""

import os
import shutil
import sys
import uuid
from pathlib import Path

SERVICE_NAME = "live-chat"
APP_VERSION = "0.1.0-beta.11"
PROTOCOL_VERSION = 1
SCHEMA_VERSION = 2
EVENT_PROTOCOL_VERSION = 2
CATALOG_SCHEMA_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 5 * 1024 * 1024


def skill_root():
    return Path(__file__).resolve().parents[2]


def chat_asset_path():
    return skill_root() / "assets" / "chat.html"


def templates_asset_path():
    return skill_root() / "assets" / "templates.json"


def default_state_dir(environment=None, os_name=None, platform_name=None, home=None):
    environment = os.environ if environment is None else environment
    os_name = os.name if os_name is None else os_name
    platform_name = sys.platform if platform_name is None else platform_name
    home = Path.home() if home is None else Path(home)
    override = environment.get("LIVE_CHAT_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os_name == "nt":
        base = environment.get("LOCALAPPDATA")
        if base:
            return Path(base) / "agent-live-chat"
        return home / "AppData" / "Local" / "agent-live-chat"
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "agent-live-chat"
    base = environment.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser() / "agent-live-chat"
    return home / ".local" / "state" / "agent-live-chat"


def legacy_default_state_dir(environment=None, os_name=None, home=None):
    """Return the former Codex-branded runtime directory for one-time migration."""
    environment = os.environ if environment is None else environment
    os_name = os.name if os_name is None else os_name
    home = Path.home() if home is None else Path(home)
    if os_name == "nt":
        base = environment.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Codex" / "live-chat"
        return home / "AppData" / "Local" / "Codex" / "live-chat"
    base = environment.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser() / "codex" / "live-chat"
    return home / ".local" / "state" / "codex" / "live-chat"


def migrate_legacy_state(target_dir, source_dir=None):
    """Copy only state.json into an empty neutral runtime directory.

    The legacy source is intentionally left untouched. Instance metadata and logs are
    never migrated because they describe a process tied to the old runtime directory.
    """
    target = Path(target_dir)
    source = Path(source_dir) if source_dir is not None else legacy_default_state_dir()
    source_state = source / "state.json"
    target_state = target / "state.json"
    if not source_state.is_file() or target_state.exists():
        return None
    if target.exists():
        try:
            next(target.iterdir())
            return None
        except StopIteration:
            pass
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / (".state.json.%s.tmp" % uuid.uuid4().hex)
    try:
        with source_state.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(str(temporary), str(target_state))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
    return source_state


def legacy_messages_path():
    override = os.environ.get("LIVE_CHAT_LEGACY_MESSAGES")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agents" / "skills" / "live-chat" / "assets" / "messages.jsonl"


def state_path(state_dir):
    return Path(state_dir) / "state.json"


def instance_path(state_dir):
    return Path(state_dir) / "instance.json"


def startup_lock_path(state_dir):
    return Path(state_dir) / "startup.lock"


def log_path(state_dir):
    return Path(state_dir) / "server.log"


def sessions_path(state_dir):
    return Path(state_dir) / "sessions.json"


def sessions_dir(state_dir):
    return Path(state_dir) / "sessions"
