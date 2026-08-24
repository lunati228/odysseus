from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.privacy_mode import (
    PrivacyConfigurationError,
    confine_path,
    normalize_profile,
    startup_capability_enabled,
    validate_database_url,
    validate_loopback_http_url,
    validate_privacy_data_root,
)


class PrivacyProfileFoundationTests(unittest.TestCase):
    def test_normal_profile_aliases_are_standard(self):
        for value in (None, "", "standard", "normal", " STANDARD "):
            with self.subTest(value=value):
                self.assertEqual(normalize_profile(value), "standard")

        self.assertEqual(normalize_profile("privacy"), "privacy")
        with self.assertRaises(PrivacyConfigurationError):
            normalize_profile("private-ish")

    def test_privacy_data_root_must_be_absolute_and_non_overlapping(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            accepted = validate_privacy_data_root(temp_dir, repo_root=repo_root)
            self.assertEqual(accepted, Path(temp_dir).resolve())

        for rejected in (
            "relative/vault",
            str(repo_root),
            str(repo_root / "privacy-vault"),
            str(repo_root.parent),
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(PrivacyConfigurationError):
                    validate_privacy_data_root(rejected, repo_root=repo_root)

        with self.assertRaises(PrivacyConfigurationError):
            validate_privacy_data_root(None, repo_root=repo_root)

    def test_confined_paths_reject_relative_and_escape_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            inside = confine_path(root / "mail" / "attachments", root, "mail attachments")
            self.assertEqual(inside, root / "mail" / "attachments")

            for rejected in (Path("relative"), root.parent / "escape"):
                with self.subTest(rejected=rejected):
                    with self.assertRaises(PrivacyConfigurationError):
                        confine_path(rejected, root, "test path")

    def test_privacy_database_url_is_file_sqlite_under_data_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            db_path = root / "database" / "app.db"
            validated = validate_database_url(
                f"sqlite:///{db_path.as_posix()}",
                data_root=root,
                profile="privacy",
            )
            self.assertEqual(validated, f"sqlite:///{db_path.resolve().as_posix()}")

            rejected = (
                "postgresql://localhost/odysseus",
                "sqlite:///:memory:",
                "sqlite:///relative.db",
                f"sqlite:///{(root.parent / 'outside.db').as_posix()}",
                f"sqlite:///file:{db_path.as_posix()}?mode=rwc&uri=true",
            )
            for url in rejected:
                with self.subTest(url=url):
                    with self.assertRaises(PrivacyConfigurationError):
                        validate_database_url(url, data_root=root, profile="privacy")

        ordinary = "postgresql://db.example/odysseus"
        self.assertEqual(
            validate_database_url(ordinary, data_root="ignored", profile="standard"),
            ordinary,
        )

    def test_loopback_http_urls_use_only_canonical_ipv4_authority(self):
        accepted = (
            "http://127.0.0.1:7000",
            "http://127.0.0.1:11434/v1/chat/completions",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertEqual(validate_loopback_http_url(url), url.rstrip("/"))

        rejected = (
            "http://0.0.0.0:7000",
            "http://127.0.0.1",
            "http://127.0.0.2:7000",
            "http://localhost:7000",
            "http://[::1]:7000",
            "http://192.168.1.2:7000",
            "http://100.64.0.1:7000",
            "https://example.com",
            "https://127.0.0.1:7443",
            "ftp://127.0.0.1/file",
            "http://user:secret@127.0.0.1:7000",
            "http://127.0.0.1:7000/?token=secret",
            "http://127.0.0.1:7000/#fragment",
        )
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(PrivacyConfigurationError):
                    validate_loopback_http_url(url)

    def test_privacy_startup_policy_disables_only_declared_background_authorities(self):
        disabled = {
            "bg_monitor",
            "mcp_connections",
            "endpoint_warmups",
            "model_keepalive",
            "default_tasks",
            "task_scheduler",
            "nightly_skill_audit",
            "cookbook_lifecycle",
        }
        for capability in disabled:
            with self.subTest(capability=capability):
                self.assertFalse(startup_capability_enabled(capability, profile="privacy"))
                self.assertTrue(startup_capability_enabled(capability, profile="standard"))

        for capability in ("local_chat", "deep_research", "upload_cleanup"):
            with self.subTest(capability=capability):
                self.assertTrue(startup_capability_enabled(capability, profile="privacy"))


if __name__ == "__main__":
    unittest.main()
