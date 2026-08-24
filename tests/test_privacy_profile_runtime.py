from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.privacy_mode import (
    PrivacyConfigurationError,
    build_profile_status,
    load_profile_dotenv,
    parse_tor_socks_endpoint,
    session_cookie_name,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_privacy_dotenv_is_optional_and_confined_to_private_data_root(tmp_path):
    calls = []

    def loader(**kwargs):
        calls.append(kwargs)
        return True

    assert load_profile_dotenv(loader, profile="privacy", data_root=tmp_path) is False
    assert calls == []

    env_file = tmp_path / "config" / "privacy.env"
    env_file.parent.mkdir()
    env_file.write_text("PRIVATE_SENTINEL=1\n", encoding="utf-8")
    assert (
        load_profile_dotenv(
            loader,
            profile="privacy",
            data_root=tmp_path,
            env_file=env_file,
            encoding="utf-8-sig",
        )
        is True
    )
    assert calls == [
        {"dotenv_path": str(env_file.resolve()), "encoding": "utf-8-sig"}
    ]

    with pytest.raises(PrivacyConfigurationError):
        load_profile_dotenv(
            loader,
            profile="privacy",
            data_root=tmp_path,
            env_file=tmp_path.parent / "repo.env",
        )


def test_standard_dotenv_keeps_the_existing_default_loader_call():
    calls = []

    def loader(**kwargs):
        calls.append(kwargs)
        return True

    assert load_profile_dotenv(loader, profile="standard", encoding="utf-8-sig") is True
    assert calls == [{"encoding": "utf-8-sig"}]


def test_tor_socks_endpoint_is_remote_dns_numeric_loopback_only():
    assert parse_tor_socks_endpoint("socks5h://127.0.0.1:19050") == (
        "127.0.0.1",
        19050,
    )


@pytest.mark.parametrize(
    "url",
    (
        "",
        "socks5://127.0.0.1:19050",
        "http://127.0.0.1:19050",
        "socks5h://localhost:19050",
        "socks5h://127.0.0.2:19050",
        "socks5h://127.0.0.1",
        "socks5h://user:secret@127.0.0.1:19050",
        "socks5h://127.0.0.1:19050/path",
        "socks5h://127.0.0.1:19050/?token=secret",
        "socks5h://127.0.0.1:19050/#fragment",
    ),
)
def test_tor_socks_endpoint_rejects_every_noncanonical_authority(url):
    with pytest.raises(PrivacyConfigurationError):
        parse_tor_socks_endpoint(url)


def test_profile_status_contract_is_nonsecret_and_fail_closed_for_tor():
    standard = build_profile_status(
        profile="standard",
        environment={
            "ODYSSEUS_PRIVACY_URL": "http://127.0.0.1:7001",
        },
    )
    assert standard == {
        "profile": "standard",
        "label": "Standard Workspace",
        "counterpart_url": "http://127.0.0.1:7001/",
        "transport": {"required": False, "ready": True, "label": "Direct"},
        "data_isolated": True,
        "session_migration": False,
        "disabled_capabilities": [],
    }

    probes = []
    privacy = build_profile_status(
        profile="privacy",
        environment={
            "ODYSSEUS_STANDARD_URL": "http://127.0.0.1:7000",
            "ODYSSEUS_TOR_SOCKS_URL": "socks5h://127.0.0.1:19050",
            "OPENAI_API_KEY": "must-never-appear",
        },
        tor_probe=lambda endpoint: probes.append(endpoint) or False,
    )
    assert privacy["profile"] == "privacy"
    assert privacy["label"] == "Privacy Workspace"
    assert privacy["counterpart_url"] == "http://127.0.0.1:7000/"
    assert privacy["transport"] == {
        "required": True,
        "ready": False,
        "label": "Tor",
    }
    assert privacy["data_isolated"] is True
    assert privacy["session_migration"] is False
    assert "cloud-models" in privacy["disabled_capabilities"]
    assert probes == [("127.0.0.1", 19050)]
    assert "must-never-appear" not in json.dumps(privacy)
    assert "socks5h" not in json.dumps(privacy)


def test_session_cookie_is_port_collision_safe_and_standard_is_unchanged():
    assert session_cookie_name(profile="standard", environment={}) == "odysseus_session"
    assert (
        session_cookie_name(
            profile="standard",
            environment={"ODYSSEUS_SESSION_COOKIE": "ignored"},
        )
        == "odysseus_session"
    )
    assert (
        session_cookie_name(profile="privacy", environment={})
        == "odysseus_privacy_session"
    )
    assert (
        session_cookie_name(
            profile="privacy",
            environment={"ODYSSEUS_SESSION_COOKIE": "private_cookie_2"},
        )
        == "private_cookie_2"
    )
    for unsafe in ("", "odysseus_session", "spaces are bad", "x" * 129):
        with pytest.raises(PrivacyConfigurationError):
            session_cookie_name(
                profile="privacy",
                environment={"ODYSSEUS_SESSION_COOKIE": unsafe},
            )


def test_app_uses_profile_dotenv_status_route_and_startup_policy():
    source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    assert "load_profile_dotenv(" in source
    assert '"/api/privacy/status"' in source
    assert '@app.get("/api/privacy/status")' in source
    for capability in (
        "bg_monitor",
        "mcp_connections",
        "endpoint_warmups",
        "model_keepalive",
        "default_tasks",
        "task_scheduler",
        "nightly_skill_audit",
        "cookbook_lifecycle",
    ):
        assert f'startup_capability_enabled("{capability}")' in source


def _privacy_subprocess(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        "ODYSSEUS_PROFILE",
        "ODYSSEUS_DATA_DIR",
        "DATABASE_URL",
        "ODYSSEUS_TOR_SOCKS_URL",
        "ODYSSEUS_SESSION_COOKIE",
    ):
        env.pop(key, None)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "ODYSSEUS_PROFILE": "privacy",
            "ODYSSEUS_DATA_DIR": str(tmp_path),
            "DATABASE_URL": f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
            "ODYSSEUS_TOR_SOCKS_URL": "socks5h://127.0.0.1:9",
        }
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_privacy_cookie_constant_uses_distinct_default(tmp_path):
    proc = _privacy_subprocess(
        "from routes.auth_routes import SESSION_COOKIE; print(SESSION_COOKIE)",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "odysseus_privacy_session"


def test_privacy_readiness_requires_tor_without_exposing_data_path(tmp_path):
    proc = _privacy_subprocess(
        "import json; from src.readiness import check_readiness; "
        "print(json.dumps(check_readiness()))",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ready"] is False
    assert payload["checks"]["tor"] == {"ok": False, "transport": "Tor"}
    assert str(tmp_path) not in json.dumps(payload)
