from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ENV_KEYS = {
    "ODYSSEUS_PROFILE",
    "ODYSSEUS_DATA_DIR",
    "ODYSSEUS_MAIL_ATTACHMENTS_DIR",
    "FASTEMBED_CACHE_PATH",
    "DATABASE_URL",
}


def _run_python(code: str, **updates: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in PROFILE_ENV_KEYS:
        env.pop(key, None)
    env.update(updates)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


class PrivacyProfilePathTests(unittest.TestCase):
    def test_every_declared_privacy_persistence_path_is_under_data_root(self):
        names = (
            "SESSIONS_FILE", "MEMORY_FILE", "PERSONAL_DIR", "RUNBOOK_DIR",
            "UPLOAD_DIR", "FEATURES_FILE", "SETTINGS_FILE", "AUTH_FILE",
            "USER_PREFS_FILE", "PRESETS_FILE", "INTEGRATIONS_FILE",
            "CONTACTS_FILE", "APP_KEY_FILE", "EMBEDDING_ENDPOINT_FILE",
            "COOKBOOK_STATE_FILE", "BG_JOBS_FILE", "VAULT_FILE",
            "TIDY_CALENDAR_STATE_FILE", "SKILLS_FILE", "APP_DB",
            "SCHEDULED_EMAILS_DB", "EMAIL_CACHE_DB", "PERSONAL_UPLOADS_DIR",
            "EMOJI_CACHE_DIR", "RAG_DIR", "CHROMA_DIR", "BG_JOBS_DIR",
            "DEEP_RESEARCH_DIR", "MCP_OAUTH_DIR", "GENERATED_IMAGES_DIR",
            "TTS_CACHE_DIR", "EMAIL_URGENCY_CACHE_DIR", "SKILLS_DIR",
            "GALLERY_DIR", "GALLERY_UPLOADS_DIR", "MEMORY_VECTORS_DIR",
            "MAIL_ATTACHMENTS_DIR", "FASTEMBED_CACHE_DIR",
        )
        code = (
            "import json, src.constants as c; "
            f"names={names!r}; "
            "print(json.dumps({'root': c.DATA_DIR, 'paths': {n: getattr(c, n) for n in names}}))"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = _run_python(
                code,
                ODYSSEUS_PROFILE="privacy",
                ODYSSEUS_DATA_DIR=temp_dir,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            root = Path(payload["root"]).resolve()
            for name, raw in payload["paths"].items():
                with self.subTest(name=name, raw=raw):
                    self.assertTrue(Path(raw).resolve().is_relative_to(root))

    def test_privacy_constants_reject_missing_relative_or_overlapping_data_root(self):
        code = "import src.constants"
        cases = (
            {},
            {"ODYSSEUS_DATA_DIR": "relative-vault"},
            {"ODYSSEUS_DATA_DIR": str(REPO_ROOT)},
            {"ODYSSEUS_DATA_DIR": str(REPO_ROOT.parent)},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                proc = _run_python(code, ODYSSEUS_PROFILE="privacy", **updates)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("PrivacyConfigurationError", proc.stderr)

    def test_privacy_constants_reject_relative_or_escaping_dedicated_overrides(self):
        code = "import src.constants"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            cases = (
                {"ODYSSEUS_MAIL_ATTACHMENTS_DIR": "relative-mail"},
                {"FASTEMBED_CACHE_PATH": "relative-fastembed"},
                {"ODYSSEUS_MAIL_ATTACHMENTS_DIR": str(root.parent / "mail")},
                {"FASTEMBED_CACHE_PATH": str(root.parent / "fastembed")},
            )
            for override in cases:
                with self.subTest(override=override):
                    proc = _run_python(
                        code,
                        ODYSSEUS_PROFILE="privacy",
                        ODYSSEUS_DATA_DIR=str(root),
                        **override,
                    )
                    self.assertNotEqual(proc.returncode, 0, proc.stdout)
                    self.assertIn("PrivacyConfigurationError", proc.stderr)

    def test_standard_profile_keeps_existing_relative_override_behavior(self):
        code = (
            "import json, src.constants as c; "
            "print(json.dumps([c.DATA_DIR, c.MAIL_ATTACHMENTS_DIR, c.FASTEMBED_CACHE_DIR]))"
        )
        proc = _run_python(
            code,
            ODYSSEUS_PROFILE="normal",
            ODYSSEUS_DATA_DIR="relative-standard-data",
            ODYSSEUS_MAIL_ATTACHMENTS_DIR="relative-mail",
            FASTEMBED_CACHE_PATH="relative-fastembed",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            ["relative-standard-data", "relative-mail", "relative-fastembed"],
        )

    def test_database_module_routes_database_url_through_profile_validator(self):
        source = (REPO_ROOT / "core" / "database.py").read_text(encoding="utf-8")
        self.assertIn("validate_database_url", source)
        self.assertIn("DATABASE_URL = validate_database_url(", source)


if __name__ == "__main__":
    unittest.main()
