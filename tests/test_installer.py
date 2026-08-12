import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    path = REPO_ROOT / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_tool("install")
packager = load_tool("package_release")


class InstallerTests(unittest.TestCase):
    def test_dry_run_does_not_write_and_maps_all_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for host in ("codex", "claude", "copilot"):
                result = installer.install(host, "user", root, root / "project")
                self.assertEqual(result["action"], "install")
                self.assertFalse(Path(result["destination"]).exists())
            self.assertEqual(
                Path(installer.install("codex", "user", root, root)["destination"]).resolve(),
                (root / ".codex" / "skills" / "live-chat").resolve(),
            )
            self.assertEqual(
                Path(installer.install("agents", "user", root, root)["destination"]).resolve(),
                (root / ".agents" / "skills" / "live-chat").resolve(),
            )

    def test_apply_and_safe_replace_create_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = installer.install("claude", "user", root, root, apply=True)
            destination = Path(first["destination"])
            self.assertTrue((destination / "SKILL.md").is_file())
            with self.assertRaises(installer.InstallError):
                installer.install("claude", "user", root, root, apply=True)
            result = installer.install(
                "claude",
                "user",
                root,
                root,
                apply=True,
                replace=True,
                now=datetime(2026, 8, 12, tzinfo=timezone.utc),
            )
            self.assertTrue(Path(result["backup"]).is_dir())
            self.assertTrue(destination.is_dir())

    def test_project_scope_uses_host_specific_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            result = installer.install("copilot", "project", project / "home", project)
            self.assertEqual(
                Path(result["destination"]).resolve(),
                (project / ".github" / "skills" / "live-chat").resolve(),
            )

    def test_post_install_doctor_failure_rolls_back_new_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(installer, "_post_install_doctor", side_effect=installer.InstallError("doctor failed")):
                with self.assertRaises(installer.InstallError):
                    installer.install("agents", "user", root, root, apply=True)
            destination = root / ".agents" / "skills" / "live-chat"
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.glob(".live-chat.install-*")), [])

    def test_auto_detection_prefers_existing_host_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".claude").mkdir()
            self.assertEqual(
                installer._detected_hosts("user", root, root / "project"),
                ["claude"],
            )
            (root / ".copilot").mkdir()
            with self.assertRaises(installer.InstallError):
                installer._detected_hosts("user", root, root / "project")
            empty = root / "empty"
            empty.mkdir()
            with self.assertRaises(installer.InstallError):
                installer._detected_hosts("user", empty, root)

    def test_project_auto_detection_requires_exact_skill_root(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".github").mkdir()
            with self.assertRaises(installer.InstallError):
                installer._detected_hosts("project", project / "home", project)
            (project / ".github" / "skills").mkdir()
            self.assertEqual(
                installer._detected_hosts("project", project / "home", project),
                ["copilot"],
            )

    def test_public_host_path_mapping(self):
        adapters = installer.HOST_ADAPTERS
        self.assertEqual(adapters["codex"]["user_fallback"], ".codex/skills")
        self.assertEqual(adapters["codex"]["project_root"], ".agents/skills")
        self.assertEqual(adapters["agents"]["user_root"], ".agents/skills")
        self.assertEqual(adapters["claude"]["project_root"], ".claude/skills")
        self.assertEqual(adapters["copilot"]["project_root"], ".github/skills")

    def test_codex_home_override_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "custom-codex"
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                result = installer.install("codex", "user", root / "home", root / "project")
            self.assertEqual(
                Path(result["destination"]).resolve(),
                (codex_home / "skills" / "live-chat").resolve(),
            )

    def test_destination_escape_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(installer.InstallError):
                installer._assert_safe_destination(root.parent / "outside", root)
            link = root / "link"
            try:
                link.symlink_to(root / "real", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaises(installer.InstallError):
                installer._assert_safe_destination(link / "live-chat", link)

    def test_release_archive_contains_only_skill_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, checksum = packager.build(Path(directory), "test")
            self.assertTrue(archive.is_file())
            self.assertTrue(checksum.is_file())
            import zipfile
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
            self.assertIn("live-chat/SKILL.md", names)
            self.assertTrue(all(name.startswith("live-chat/") for name in names))
            self.assertFalse(any("tests/" in name or "__pycache__" in name for name in names))
