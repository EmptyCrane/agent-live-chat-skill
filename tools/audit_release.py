#!/usr/bin/env python3
"""Audit tracked source, archives, and optional Git publication metadata."""

import argparse
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "live-chat"
SKILL_ENTRIES = {"SKILL.md", "agents", "assets", "scripts", "references"}
TEXT_SUFFIXES = {".py", ".md", ".html", ".yaml", ".yml", ".json", ".js", ".txt"}
TEXT_NAMES = {"LICENSE", ".gitignore", ".gitattributes"}
FORBIDDEN_NAMES = {"state.json", "instance.json", "server.log", "messages.jsonl"}

# These patterns describe categories of private data without embedding a real
# username, home directory, workspace, address, or credential in the scanner.
WINDOWS_LOCAL_PATH = re.compile(
    r"(?i)(?<![A-Z0-9_])(?:[A-Z]:[\\/](?:users|workspace)(?:[\\/][^\s'\"`<>|]+)+)"
)
POSIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[A-Za-z0-9._-]+(?:/[^\s'\"`<>|]+)+")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
EMAIL = re.compile(r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])")
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"][^'\"]{8,}"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
)
FORBIDDEN_PNG_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}


def _tracked_paths():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(ROOT),
            capture_output=True,
            check=True,
        )
        names = [value for value in result.stdout.decode("utf-8").split("\0") if value]
        if names:
            return [ROOT / PurePosixPath(name) for name in names]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        pass
    return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def _valid_ipv4(value):
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _audit_text(text, label, errors):
    if WINDOWS_LOCAL_PATH.search(text) or POSIX_HOME_PATH.search(text):
        errors.append("Concrete local absolute path found in %s" % label)
    for address in IPV4.findall(text):
        if _valid_ipv4(address) and address != "127.0.0.1":
            errors.append("Non-loopback IPv4 address found in %s" % label)
            break
    for email in EMAIL.findall(text):
        if not email.lower().endswith("@users.noreply.github.com"):
            errors.append("Non-noreply email address found in %s" % label)
            break
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        errors.append("Possible hard-coded credential found in %s" % label)


def _audit_png(data, label, errors):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        errors.append("Invalid PNG file: %s" % label)
        return
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            errors.append("Truncated PNG file: %s" % label)
            return
        if kind in FORBIDDEN_PNG_CHUNKS:
            errors.append("PNG metadata chunk %s found in %s" % (kind.decode("ascii"), label))
        offset = end
        if kind == b"IEND":
            return
    errors.append("PNG is missing IEND: %s" % label)


def _audit_bytes(data, name, label, errors):
    path = PurePosixPath(name)
    if path.name in FORBIDDEN_NAMES:
        errors.append("Runtime artifact included: %s" % label)
    if path.suffix == ".pyc" or "__pycache__" in path.parts:
        errors.append("Python cache included: %s" % label)
    if data.startswith(b"\xef\xbb\xbf"):
        errors.append("UTF-8 BOM found: %s" % label)
    if path.suffix.lower() == ".png":
        _audit_png(data, label, errors)
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append("Non-UTF-8 text file: %s" % label)
        else:
            _audit_text(text, label, errors)


def audit(paths=None):
    errors = []
    if {item.name for item in SKILL.iterdir()} != SKILL_ENTRIES:
        errors.append("Skill root does not match the runtime whitelist")
    for path in paths or _tracked_paths():
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        _audit_bytes(path.read_bytes(), relative, relative, errors)
    if errors:
        raise RuntimeError("\n".join(errors))
    return True


def audit_archive(path):
    errors = []
    path = Path(path)
    with zipfile.ZipFile(path) as bundle:
        for info in bundle.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                errors.append("Unsafe archive member: %s" % info.filename)
                continue
            if not info.is_dir():
                _audit_bytes(bundle.read(info), info.filename, "%s:%s" % (path.name, info.filename), errors)
    if errors:
        raise RuntimeError("\n".join(errors))
    return True


def audit_files(paths):
    errors = []
    for path in (Path(value) for value in paths):
        _audit_bytes(path.read_bytes(), path.name, path.name, errors)
    if errors:
        raise RuntimeError("\n".join(errors))
    return True


def audit_git_history(expected_name=None, require_noreply=False, single_root=False):
    format_string = "%H%x00%P%x00%an%x00%ae%x00%cn%x00%ce"
    result = subprocess.run(
        ["git", "log", "--all", "--format=" + format_string],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    records = [line.split("\0") for line in result.stdout.splitlines() if line]
    errors = []
    if single_root and (len(records) != 1 or records[0][1]):
        errors.append("Git publication history must contain exactly one root commit")
    for record in records:
        _, _, author_name, author_email, committer_name, committer_email = record
        if expected_name and (author_name != expected_name or committer_name != expected_name):
            errors.append("Unexpected Git author or committer name")
        if require_noreply and not all(
            email.lower().endswith("@users.noreply.github.com")
            for email in (author_email, committer_email)
        ):
            errors.append("Git author and committer emails must use GitHub noreply")
    if not records:
        errors.append("Git publication history is empty")
    if errors:
        raise RuntimeError("\n".join(sorted(set(errors))))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", type=Path, default=[])
    parser.add_argument("--file", action="append", type=Path, default=[])
    parser.add_argument("--expected-git-name")
    parser.add_argument("--require-noreply", action="store_true")
    parser.add_argument("--single-root", action="store_true")
    args = parser.parse_args(argv)
    audit()
    for archive in args.archive:
        audit_archive(archive)
    if args.file:
        audit_files(args.file)
    if args.expected_git_name or args.require_noreply or args.single_root:
        audit_git_history(args.expected_git_name, args.require_noreply, args.single_root)
    print("Release audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
