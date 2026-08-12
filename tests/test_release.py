import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "live-chat"
ENTRY = SKILL / "scripts" / "live_chat.py"


class ReleaseTests(unittest.TestCase):
    def test_cli_version_and_runtime_whitelist(self):
        result = subprocess.run(
            [sys.executable, str(ENTRY), "--version"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "0.1.0-beta.4")
        self.assertEqual(
            {item.name for item in SKILL.iterdir()},
            {"SKILL.md", "agents", "assets", "scripts", "references"},
        )

    def test_capability_fallback_is_explicit_and_does_not_simulate(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        hosts = (SKILL / "references" / "hosts.md").read_text(encoding="utf-8")
        self.assertIn("Never generate several fictional agents", skill)
        self.assertIn("actual completion order", skill)
        self.assertIn("in-flight work cannot be force-cancelled", skill)
        self.assertIn("Never label single-agent role simulation", hosts)
        self.assertIn("Do not infer host availability", skill)
        self.assertIn("model override", hosts)
        self.assertNotIn("Luna", skill + hosts)

    def test_static_release_audit(self):
        path = ROOT / "tools" / "audit_release.py"
        spec = importlib.util.spec_from_file_location("audit_release", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module.audit())

    def test_privacy_audit_detects_categories_without_storing_personal_values(self):
        path = ROOT / "tools" / "audit_release.py"
        spec = importlib.util.spec_from_file_location("audit_release_categories", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        samples = [
            "C:" + "\\" + "Users" + "\\" + "Example" + "\\" + "project",
            "192" + ".168.10.20",
            "person" + "@" + "example.com",
        ]
        for sample in samples:
            errors = []
            module._audit_text(sample, "sample", errors)
            self.assertTrue(errors, sample)

    def test_release_archive_passes_the_same_privacy_audit(self):
        audit_path = ROOT / "tools" / "audit_release.py"
        audit_spec = importlib.util.spec_from_file_location("audit_release_archive", audit_path)
        audit_module = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(audit_module)
        package_path = ROOT / "tools" / "package_release.py"
        package_spec = importlib.util.spec_from_file_location("package_release_audit", package_path)
        package_module = importlib.util.module_from_spec(package_spec)
        package_spec.loader.exec_module(package_module)
        with tempfile.TemporaryDirectory() as directory:
            archive, _ = package_module.build(Path(directory), "audit")
            self.assertTrue(audit_module.audit_archive(archive))

    def test_readmes_use_locale_specific_screenshots(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("v0.1.0-beta.4/live-chat-en.png", readme)
        self.assertIn("v0.1.0-beta.4/live-chat-zh-CN.png", chinese)
        self.assertNotIn("docs/images/", readme + chinese)
