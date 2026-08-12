#!/usr/bin/env python3
"""Preview or install the bundled live-chat skill for supported hosts."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "live-chat"
REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "skill" / SKILL_NAME
RUNTIME_ENTRIES = {"SKILL.md", "agents", "assets", "scripts", "references"}

sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE / "scripts"))
from live_chat_core.adapters import HOST_ADAPTERS, skill_root_for  # noqa: E402

INSTALL_HOSTS = ("codex", "agents", "claude", "copilot")


class InstallError(RuntimeError):
    pass


def _is_link_like(path):
    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_relative_to(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_present(path):
    return path.exists() or _is_link_like(path)


def _assert_component_chain(path, boundary, label):
    current = path.expanduser().absolute()
    boundary = boundary.expanduser().absolute()
    if not _is_relative_to(current, boundary):
        raise InstallError("%s escapes its boundary: %s" % (label, current))
    while True:
        if _is_link_like(current):
            raise InstallError("%s contains a symbolic link or reparse point: %s" % (label, current))
        if current == boundary:
            break
        current = current.parent


def _assert_hashable_tree(root, label):
    if not root.is_dir() or _is_link_like(root):
        raise InstallError("%s must be a real directory: %s" % (label, root))
    for item in root.rglob("*"):
        if _is_link_like(item):
            raise InstallError("%s contains a symbolic link or reparse point: %s" % (label, item))


def _assert_plain_tree(root):
    _assert_hashable_tree(root, "skill source")
    entries = {item.name for item in root.iterdir()}
    if entries != RUNTIME_ENTRIES:
        raise InstallError("skill source entries do not match the release whitelist")
    for item in root.rglob("*"):
        if _is_link_like(item):
            raise InstallError("symbolic links are not allowed: %s" % item)
        if item.name == "__pycache__" or item.suffix == ".pyc":
            raise InstallError("generated Python caches are not installable: %s" % item)


def _assert_safe_destination(destination, base):
    raw_base = base.expanduser().absolute()
    raw_destination = destination.expanduser().absolute()
    if not _is_relative_to(raw_destination, raw_base) or raw_destination == raw_base:
        raise InstallError("destination escapes its host skill root: %s" % raw_destination)
    _assert_component_chain(raw_destination, raw_base, "destination")
    resolved_base = raw_base.resolve()
    resolved_destination = raw_destination.resolve(strict=False)
    if not _is_relative_to(resolved_destination, resolved_base):
        raise InstallError("resolved destination escapes its host skill root")
    return resolved_destination, resolved_base


def _backup_root_for(skill_root):
    return skill_root.parent / "skill-backups"


def _assert_safe_backup(backup, backup_root, config_root):
    raw_config = config_root.expanduser().absolute()
    raw_root = backup_root.expanduser().absolute()
    raw_backup = backup.expanduser().absolute()
    if raw_root == raw_config or not _is_relative_to(raw_root, raw_config):
        raise InstallError("backup root escapes its host configuration directory")
    if raw_backup == raw_root or not _is_relative_to(raw_backup, raw_root):
        raise InstallError("backup path escapes its backup root")
    _assert_component_chain(raw_root, raw_config, "backup root")
    _assert_component_chain(raw_backup, raw_root, "backup path")
    resolved_config = raw_config.resolve()
    resolved_root = raw_root.resolve(strict=False)
    resolved_backup = raw_backup.resolve(strict=False)
    if not _is_relative_to(resolved_root, resolved_config):
        raise InstallError("resolved backup root escapes its host configuration directory")
    if not _is_relative_to(resolved_backup, resolved_root):
        raise InstallError("resolved backup path escapes its backup root")
    return resolved_backup, resolved_root, resolved_config


def _detected_hosts(scope, home, project_root):
    detected = []
    for host in INSTALL_HOSTS:
        root = skill_root_for(host, scope, home, project_root, os.environ)
        marker = root.parent if scope == "user" else root
        if marker.is_dir():
            detected.append(host)
    if len(detected) != 1:
        detail = "none" if not detected else ", ".join(detected)
        raise InstallError(
            "host auto-detection is ambiguous (%s); choose an explicit --host" % detail
        )
    return detected


def _tree_hashes(root):
    _assert_hashable_tree(Path(root), "hash source")
    values = {}
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        values[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def _verify_runtime(root):
    _assert_plain_tree(root)
    command = [sys.executable, "-B", str(Path(root) / "scripts" / "live_chat.py"), "--version"]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=environment,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise InstallError("installed runtime version check failed")


def _post_install_doctor(root):
    """Run the installed copy's own structural and state diagnostics."""
    with tempfile.TemporaryDirectory(prefix="live-chat-install-doctor-") as state_dir:
        command = [
            sys.executable,
            "-B",
            str(Path(root) / "scripts" / "live_chat.py"),
            "--state-dir",
            state_dir,
            "--json",
            "doctor",
            "--host",
            "generic",
            "--port",
            "0",
        ]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUTF8"] = "1"
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
            env=environment,
        )
    try:
        report = json.loads(result.stdout)
    except ValueError as exc:
        raise InstallError("post-install doctor returned invalid JSON") from exc
    if result.returncode not in (0, 2) or not report.get("ok"):
        raise InstallError("post-install doctor failed")
    return report


def install(host, scope, home, project_root, apply=False, replace=False, now=None):
    _assert_plain_tree(SOURCE)
    base = skill_root_for(host, scope, home, project_root, os.environ)
    destination, base = _assert_safe_destination(base / SKILL_NAME, base)
    config_root = base.parent
    backup_root = _backup_root_for(base)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / ("%s-%s" % (SKILL_NAME, stamp))
    backup, backup_root, config_root = _assert_safe_backup(
        backup, backup_root, config_root
    )
    result = {
        "host": host,
        "scope": scope,
        "destination": str(destination),
        "backup": str(backup),
        "action": "install",
    }
    replacing = _path_present(destination)
    if replacing:
        if _is_link_like(destination) or not destination.is_dir():
            raise InstallError("existing destination is not a real directory: %s" % destination)
        if not replace:
            raise InstallError("destination already exists; use --replace to back it up")
        _assert_hashable_tree(destination, "existing destination")
        if _path_present(backup):
            raise InstallError("backup path already exists: %s" % backup)
        result["action"] = "replace"
    if not apply:
        return result
    config_root.mkdir(parents=True, exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    _assert_safe_destination(destination, base)
    _assert_safe_backup(backup, backup_root, config_root)
    source_hashes = _tree_hashes(SOURCE)
    old_hashes = _tree_hashes(destination) if replacing else None
    staging = config_root / (".%s.install-%s" % (SKILL_NAME, uuid.uuid4().hex))
    restore_staging = config_root / (".%s.restore-%s" % (SKILL_NAME, uuid.uuid4().hex))
    _assert_component_chain(staging, config_root, "staging path")
    _assert_component_chain(restore_staging, config_root, "restore staging path")
    try:
        shutil.copytree(SOURCE, staging, copy_function=shutil.copy2)
        if source_hashes != _tree_hashes(staging):
            raise InstallError("staged installation hash verification failed")
        _verify_runtime(staging)
        if result["action"] == "replace":
            backup_root.mkdir(parents=True, exist_ok=True)
            _assert_safe_backup(backup, backup_root, config_root)
            if _path_present(backup):
                raise InstallError("backup path already exists: %s" % backup)
            destination.rename(backup)
            if old_hashes != _tree_hashes(backup):
                raise InstallError("backup hash verification failed")
        staging.rename(destination)
        if source_hashes != _tree_hashes(destination):
            raise InstallError("installed file hash verification failed")
        result["doctor"] = _post_install_doctor(destination)
    except Exception as exc:
        if _path_present(staging):
            _assert_component_chain(staging, config_root, "staging cleanup")
            shutil.rmtree(staging)
        if result["action"] == "install":
            if _path_present(destination):
                if _tree_hashes(destination) != source_hashes:
                    raise InstallError(
                        "installation failed and candidate hash changed; preserving destination"
                    ) from exc
                shutil.rmtree(destination)
            raise
        if not _path_present(backup):
            raise InstallError("replacement failed before a recoverable backup was created") from exc
        if _tree_hashes(backup) != old_hashes:
            raise InstallError("replacement failed and backup hash verification failed; backup preserved") from exc
        if _path_present(destination):
            if _tree_hashes(destination) != source_hashes:
                raise InstallError(
                    "replacement failed and candidate hash changed; backup preserved"
                ) from exc
            shutil.rmtree(destination)
        try:
            shutil.copytree(backup, restore_staging, copy_function=shutil.copy2)
            if _tree_hashes(restore_staging) != old_hashes:
                raise InstallError("restored staging hash verification failed")
            restore_staging.rename(destination)
            if _tree_hashes(destination) != old_hashes:
                raise InstallError("restored destination hash verification failed")
        except Exception as restore_exc:
            if _path_present(restore_staging):
                if _tree_hashes(restore_staging) == old_hashes:
                    shutil.rmtree(restore_staging)
            raise InstallError("replacement rollback failed; backup preserved: %s" % restore_exc) from exc
        raise
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="Safely install the live-chat Agent Skill")
    parser.add_argument(
        "--host",
        choices=("auto",) + INSTALL_HOSTS + ("all",),
        default="auto",
        help="target host; auto requires exactly one existing host Skill root",
    )
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true", help="perform writes; otherwise preview only")
    parser.add_argument("--replace", action="store_true", help="back up and replace an existing skill")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    home = Path(os.environ.get("HOME") or Path.home()).expanduser().resolve()
    project_root = args.project_root.expanduser().resolve()
    try:
        if args.host == "all":
            hosts = ["codex", "claude", "copilot"]
        elif args.host == "auto":
            hosts = _detected_hosts(args.scope, home, project_root)
        else:
            hosts = [args.host]
        if args.apply:
            for host in hosts:
                install(host, args.scope, home, project_root, False, args.replace)
        results = [install(host, args.scope, home, project_root, args.apply, args.replace) for host in hosts]
    except (InstallError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    mode = "APPLY" if args.apply else "DRY-RUN"
    for result in results:
        line = "%s %s %s -> %s" % (mode, result["action"], result["host"], result["destination"])
        if result.get("backup"):
            line += " (backup: %s)" % result["backup"]
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
