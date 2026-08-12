#!/usr/bin/env python3
"""Build a deterministic Skill-only ZIP and SHA-256 checksum."""

import argparse
import hashlib
import os
import stat
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "skill" / "live-chat"
ALLOWED = {"SKILL.md", "agents", "assets", "scripts", "references"}


def is_link_like(path):
    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def source_files():
    if {item.name for item in SOURCE.iterdir()} != ALLOWED:
        raise RuntimeError("release source does not match the install whitelist")
    files = []
    for item in SOURCE.rglob("*"):
        if is_link_like(item):
            raise RuntimeError("release source contains a symbolic link: %s" % item)
        if item.is_file():
            if item.suffix == ".pyc" or "__pycache__" in item.parts:
                raise RuntimeError("release source contains Python cache: %s" % item)
            files.append(item)
    return sorted(files, key=lambda value: value.as_posix())


def build(output_dir, version):
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / ("live-chat-%s.zip" % version)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in source_files():
            relative = path.relative_to(SOURCE)
            info = zipfile.ZipInfo((Path("live-chat") / relative).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes(), compresslevel=9)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    with checksum.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("%s  %s\n" % (digest, archive.name))
    return archive, checksum


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0-beta.5")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    args = parser.parse_args(argv)
    archive, checksum = build(args.output_dir.resolve(), args.version)
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
