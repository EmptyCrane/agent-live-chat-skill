#!/usr/bin/env python3
"""Preview or install the bundled live-chat skill for supported hosts."""

import argparse
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "live-chat"
REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "skill" / SKILL_NAME
HOST_DIRS = {
    "codex": {"user": Path(".agents/skills"), "project": Path(".agents/skills")},
    "claude": {"user": Path(".claude/skills"), "project": Path(".claude/skills")},
    "copilot": {"user": Path(".copilot/skills"), "project": Path(".github/skills")},
}
RUNTIME_ENTRIES = {"SKILL.md", "agents", "assets", "scripts", "references"}


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


def _assert_plain_tree(root):
    if not root.is_dir() or _is_link_like(root):
        raise InstallError("skill source must be a real directory: %s" % root)
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
    current = raw_destination
    while current != raw_base:
        # Check link metadata directly: Path.exists() is false for a dangling
        # symlink, but dangling links are unsafe destination components too.
        if _is_link_like(current):
            raise InstallError("destination contains a symbolic link: %s" % current)
        current = current.parent
    if _is_link_like(raw_base):
        raise InstallError("host skill root cannot be a symbolic link: %s" % raw_base)
    resolved_base = raw_base.resolve()
    resolved_destination = raw_destination.resolve(strict=False)
    if not _is_relative_to(resolved_destination, resolved_base):
        raise InstallError("resolved destination escapes its host skill root")
    return resolved_destination, resolved_base


def _detected_hosts(scope, home, project_root):
    detected = []
    for host, paths in HOST_DIRS.items():
        anchor = home if scope == "user" else project_root
        marker = anchor / (paths[scope].parts[0] if scope == "user" else paths[scope])
        if marker.is_dir():
            detected.append(host)
    if len(detected) != 1:
        detail = "none" if not detected else ", ".join(detected)
        raise InstallError(
            "host auto-detection is ambiguous (%s); choose --host codex, claude, or copilot" % detail
        )
    return detected


def install(host, scope, home, project_root, apply=False, replace=False, now=None):
    _assert_plain_tree(SOURCE)
    anchor = home if scope == "user" else project_root
    base = anchor / HOST_DIRS[host][scope]
    destination, base = _assert_safe_destination(base / SKILL_NAME, base)
    result = {"host": host, "scope": scope, "destination": str(destination), "action": "install"}
    if destination.exists():
        if _is_link_like(destination) or not destination.is_dir():
            raise InstallError("existing destination is not a real directory: %s" % destination)
        if not replace:
            raise InstallError("destination already exists; use --replace to back it up")
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        backup = destination.with_name("%s.backup-%s" % (SKILL_NAME, stamp))
        if backup.exists():
            raise InstallError("backup path already exists: %s" % backup)
        result.update({"action": "replace", "backup": str(backup)})
    if not apply:
        return result
    base.mkdir(parents=True, exist_ok=True)
    if result["action"] == "replace":
        destination.rename(Path(result["backup"]))
    try:
        shutil.copytree(SOURCE, destination, copy_function=shutil.copy2)
    except Exception:
        if result["action"] == "replace" and not destination.exists():
            Path(result["backup"]).rename(destination)
        raise
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="Safely install the live-chat Agent Skill")
    parser.add_argument(
        "--host",
        choices=("auto", "codex", "claude", "copilot", "all"),
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
            hosts = list(HOST_DIRS)
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
