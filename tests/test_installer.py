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
            for host in ("codex", "agents", "claude", "copilot"):
                result = installer.install(host, "user", root, root / "project")
                self.assertEqual(result["action"], "install")
                self.assertFalse(Path(result["destination"]).exists())
                self.assertFalse(Path(result["backup"]).exists())
                self.assertEqual(Path(result["backup"]).parent.name, "skill-backups")
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
            self.assertFalse(
                Path(result["backup"]).is_relative_to(destination.parent)
            )
            self.assertEqual(
                installer._tree_hashes(Path(result["backup"])),
                installer._tree_hashes(destination),
            )

    def test_user_backups_are_outside_every_skill_discovery_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for host in ("codex", "agents", "claude", "copilot"):
                with self.subTest(host=host):
                    first = installer.install(host, "user", root, root / "project", apply=True)
                    result = installer.install(
                        host,
                        "user",
                        root,
                        root / "project",
                        apply=True,
                        replace=True,
                        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
                    )
                    destination = Path(first["destination"])
                    backup = Path(result["backup"])
                    self.assertFalse(backup.is_relative_to(destination.parent))
                    self.assertEqual(backup.parent, destination.parent.parent / "skill-backups")

    def test_project_backups_are_outside_host_skill_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for host in ("codex", "agents", "claude", "copilot"):
                with self.subTest(host=host):
                    project = root / host
                    project.mkdir()
                    first = installer.install(host, "project", project / "home", project, apply=True)
                    result = installer.install(
                        host,
                        "project",
                        project / "home",
                        project,
                        apply=True,
                        replace=True,
                        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
                    )
                    destination = Path(first["destination"])
                    backup = Path(result["backup"])
                    self.assertFalse(backup.is_relative_to(destination.parent))
                    self.assertEqual(backup.parent, destination.parent.parent / "skill-backups")

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

    def test_post_install_doctor_failure_restores_old_tree_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = installer.install("agents", "user", root, root, apply=True)
            destination = Path(first["destination"])
            marker = destination / "local-marker.txt"
            marker.write_text("old installation\n", encoding="utf-8")
            old_hashes = installer._tree_hashes(destination)
            with patch.object(installer, "_post_install_doctor", side_effect=installer.InstallError("doctor failed")):
                with self.assertRaises(installer.InstallError):
                    installer.install(
                        "agents",
                        "user",
                        root,
                        root,
                        apply=True,
                        replace=True,
                        now=datetime(2026, 8, 14, tzinfo=timezone.utc),
                    )
            backup = root / ".agents" / "skill-backups" / "live-chat-20260814T000000Z"
            self.assertEqual(installer._tree_hashes(destination), old_hashes)
            self.assertEqual(installer._tree_hashes(backup), old_hashes)
            self.assertTrue(marker.is_file())

    def test_existing_backup_target_fails_without_changing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = installer.install("claude", "user", root, root, apply=True)
            destination = Path(first["destination"])
            old_hashes = installer._tree_hashes(destination)
            backup = root / ".claude" / "skill-backups" / "live-chat-20260815T000000Z"
            backup.mkdir(parents=True)
            with self.assertRaises(installer.InstallError):
                installer.install(
                    "claude",
                    "user",
                    root,
                    root,
                    apply=True,
                    replace=True,
                    now=datetime(2026, 8, 15, tzinfo=timezone.utc),
                )
            self.assertEqual(installer._tree_hashes(destination), old_hashes)

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

    def test_backup_symlink_or_reparse_point_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = installer.install("agents", "user", root, root, apply=True)
            destination = Path(first["destination"])
            backup_root = root / ".agents" / "skill-backups"
            old_hashes = installer._tree_hashes(destination)
            original = installer._is_link_like

            def mark_backup_root(path):
                return Path(path).name == backup_root.name or original(path)

            with patch.object(installer, "_is_link_like", side_effect=mark_backup_root):
                with self.assertRaises(installer.InstallError):
                    installer.install(
                        "agents", "user", root, root, apply=True, replace=True
                    )
            self.assertEqual(installer._tree_hashes(destination), old_hashes)

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
