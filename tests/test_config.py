import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skill" / "live-chat" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_chat_core.config import (  # noqa: E402
    APP_VERSION,
    default_state_dir,
    legacy_default_state_dir,
    migrate_legacy_state,
)


class ConfigTests(unittest.TestCase):
    def test_neutral_state_paths_for_supported_platforms(self):
        home = Path("home")
        windows = default_state_dir(
            {"LOCALAPPDATA": "local"}, os_name="nt", platform_name="win32", home=home
        )
        mac = default_state_dir({}, os_name="posix", platform_name="darwin", home=home)
        linux = default_state_dir(
            {"XDG_STATE_HOME": "xdg"}, os_name="posix", platform_name="linux", home=home
        )
        fallback = default_state_dir({}, os_name="posix", platform_name="linux", home=home)
        self.assertEqual(windows.parts[-1], "agent-live-chat")
        self.assertEqual(mac.parts[-3:], ("Library", "Application Support", "agent-live-chat"))
        self.assertEqual(linux.parts[-2:], ("xdg", "agent-live-chat"))
        self.assertEqual(fallback.parts[-3:], (".local", "state", "agent-live-chat"))

    def test_environment_override_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                default_state_dir(
                    {"LIVE_CHAT_STATE_DIR": directory},
                    os_name="nt",
                    platform_name="win32",
                    home=Path("ignored"),
                ),
                Path(directory).resolve(),
            )

    def test_legacy_state_copy_is_one_time_and_non_destructive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old"
            new = root / "new"
            old.mkdir()
            source = old / "state.json"
            source.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            (old / "instance.json").write_text("{}", encoding="utf-8")
            migrated = migrate_legacy_state(new, old)
            self.assertEqual(migrated, source)
            self.assertEqual((new / "state.json").read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
            self.assertFalse((new / "instance.json").exists())
            self.assertTrue(source.exists())
            (new / "state.json").write_text('{"kept":true}', encoding="utf-8")
            self.assertIsNone(migrate_legacy_state(new, old))
            self.assertIn("kept", (new / "state.json").read_text(encoding="utf-8"))

    def test_version_is_beta(self):
        self.assertEqual(APP_VERSION, "0.1.0-beta.8")

    def test_legacy_path_remains_codex_branded(self):
        path = legacy_default_state_dir(
            {"XDG_STATE_HOME": "xdg"}, os_name="posix", home=Path("home")
        )
        self.assertEqual(path.parts[-3:], ("xdg", "codex", "live-chat"))
