"""Additional manager guarantees beyond the pinned static contract.

``tests/test_privacy_manager_script.py`` pins the shape of the installed
PowerShell manager.  Its own audit
(``BACKLOG-PRIVACY-WORKSPACE-FORK.md``) records limits that the pinned suite
cannot catch, plus one class of defect that only a real invocation can prove.
Both are covered here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest


PRIVATE_HOME = Path(
    os.environ.get("ODYSSEUS_PRIVATE_HOME", Path.home() / ".odysseus-private")
)
MANAGER = Path(
    os.environ.get(
        "ODYSSEUS_MANAGER_TEST_PATH",
        PRIVATE_HOME / "bin" / "Odysseus-Private.ps1",
    )
)
TOR_STATE = PRIVATE_HOME / "privacy-vault" / "run" / "tor.json"

pytestmark = pytest.mark.skipif(
    not MANAGER.is_file(),
    reason="the external Odysseus-Private manager installation is not present",
)


def _source() -> str:
    return MANAGER.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^function\s+{re.escape(name)}\b.*?(?=^function\s+|^switch\s*\(|\Z)",
        source,
    )
    assert match is not None, f"missing PowerShell function {name}"
    return match.group(0).rstrip()


# ---------------------------------------------------------------------------
# audit gap 1: a size-only alias would satisfy the pinned suite
# ---------------------------------------------------------------------------


def test_assert_file_identity_actually_hashes_and_compares_case_insensitively():
    body = _function(_source(), "Assert-FileIdentity")

    assert "SHA256" in body, (
        "Assert-FileIdentity must hash, not just compare size; a swapped "
        "binary of equal length would otherwise pass"
    )
    assert "ComputeHash" in body
    assert "OrdinalIgnoreCase" in body, (
        "hex digest casing varies by producer; the compare must ignore case"
    )
    assert "PSIsContainer" in body, "a directory must be refused, not hashed"


# ---------------------------------------------------------------------------
# audit gap 2: the scrubber must not rewrite the operator's real environment
# ---------------------------------------------------------------------------


def test_the_secret_scrubber_only_touches_process_scope():
    body = _function(_source(), "Clear-PrivateChildSecretsAndProxies")

    assert "[EnvironmentVariableTarget]::Process" in body
    assert "[EnvironmentVariableTarget]::User" not in body, (
        "clearing User scope would persistently delete the operator's real "
        "API keys, far outside this script's authority"
    )
    assert "[EnvironmentVariableTarget]::Machine" not in body, (
        "clearing Machine scope would affect every account on the host"
    )


# ---------------------------------------------------------------------------
# audit gap 3: privacy start must never touch admin credentials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "function_name",
    [
        "Start-Private",
        "Start-PrivacyApp",
        "Set-PrivacyAppEnvironment",
        "Clear-PrivateChildSecretsAndProxies",
    ],
)
def test_the_privacy_start_path_never_reads_credentials_or_logs_in(function_name):
    body = _function(_source(), function_name)

    for forbidden in ("CredentialPath", "Sync-Endpoint", "/api/auth/login", "password"):
        assert forbidden not in body, (
            f"{function_name} must not reference {forbidden!r}: the privacy "
            "profile has its own auth state and must not reuse the standard "
            "workspace admin credential"
        )


# ---------------------------------------------------------------------------
# audit gap 4: status must not leak, and the arms must not be inert
# ---------------------------------------------------------------------------


def test_status_emits_no_secret_or_log_path_keys():
    body = _function(_source(), "Show-Status")

    # Redirected log paths are not secrets, but they are the fastest route to
    # private content on disk, so they stay out of the most-copied output.
    for forbidden in ("stdout", "stderr", "credential", "password",
                      "apiKey", "api_key", "cookie", "token"):
        assert not re.search(rf"(?mi)^\s*{forbidden}\s*=", body), (
            f"Show-Status must not emit {forbidden!r} as a key"
        )


def test_each_declared_privacy_action_dispatches_to_a_real_function():
    """The pinned suite only checks the action string appears somewhere.

    A patch that adds the ValidateSet entry but no switch arm satisfies that
    and silently falls through to Show-Status -- the fails-open trap this
    manager already shipped once.
    """
    source = _source()
    switch_block = source[source.index("\nswitch ("):]

    for arm in (
        '"start-tor" { Start-Tor }',
        '"stop-tor" { Stop-Tor }',
        '"start-private" { Start-Private }',
        '"stop-private" { Stop-PrivacyApp }',
    ):
        assert arm in switch_block, f"missing or inert switch arm: {arm}"

    assert "default { Show-Status }" in switch_block


def test_stopping_the_private_app_does_not_also_stop_tor():
    source = _source()
    switch_block = source[source.index("\nswitch ("):]
    stop_private_arm = re.search(
        r'"stop-private"\s*\{([^}]*)\}', switch_block
    )
    assert stop_private_arm is not None
    assert "Stop-Tor" not in stop_private_arm.group(1), (
        "stop-private must not silently kill the shared, slow-to-bootstrap "
        "Tor sidecar; stopping Tor is an explicit operator action"
    )


# ---------------------------------------------------------------------------
# audit gap 5: fail-closed start, proven by structure
# ---------------------------------------------------------------------------


def test_start_tor_refuses_an_unverified_config_and_leaves_no_partial_state():
    body = _function(_source(), "Start-Tor")

    verify_at = body.index("$verify.ExitCode -ne 0")
    state_at = body.index("Write-JsonNoBom $TorStatePath")
    bootstrap_at = body.index("Bootstrapped\\s+100%")

    assert verify_at < bootstrap_at < state_at, (
        "order must be: verify config, then prove bootstrap, then record "
        "ownership -- a state file written earlier would let Assert-TorReady "
        "believe a half-started Tor is usable"
    )
    assert "Stop-Process" in body, (
        "a failed bootstrap must kill the process it started, not orphan it"
    )


def test_tor_readiness_requires_more_than_a_listening_socket():
    body = _function(_source(), "Assert-TorReady")

    assert "Assert-OwnedProcessIdentity" in body
    assert "TcpClient" in body, (
        "readiness must prove the SOCKS port accepts a connection"
    )


# ---------------------------------------------------------------------------
# runtime behavior: the one thing no static check can prove
# ---------------------------------------------------------------------------


def _tor_state_exists() -> bool:
    try:
        return TOR_STATE.exists()
    except OSError:
        return True


@pytest.mark.skipif(
    _tor_state_exists(),
    reason="Tor state exists; refusing to exercise the no-state stop path",
)
def test_stop_tor_with_no_state_is_idempotent_and_does_not_throw():
    """A stop with nothing to stop must report and exit 0, never throw.

    ``$ErrorActionPreference = "Stop"`` makes any unguarded error fatal, so a
    missing state file is exactly the case that turns a harmless no-op into a
    non-zero exit for the operator.
    """
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(MANAGER),
            "-Action", "stop-tor",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert completed.returncode == 0, (
        f"stop-tor with no state exited {completed.returncode}: "
        f"{completed.stderr}"
    )
    assert "not owned/running" in completed.stdout


def test_status_is_valid_json_with_the_privacy_fields_present():
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(MANAGER),
            "-Action", "status",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr

    payload = json.loads(completed.stdout)

    for field in ("torRunning", "torPid", "torSocksUrl",
                  "privateAppRunning", "privateAppPid", "privateAppUrl"):
        assert field in payload, f"status is missing {field}"

    assert payload["torSocksUrl"] == "socks5h://127.0.0.1:19050"
    assert payload["privateAppUrl"] == "http://127.0.0.1:7001"
    assert isinstance(payload["torRunning"], bool)
    assert isinstance(payload["privateAppRunning"], bool)

    # No secret-bearing key may appear in real output, not just in source.
    lowered = {key.lower() for key in payload}
    for forbidden in ("credential", "password", "apikey", "api_key",
                      "cookie", "token", "stdout", "stderr"):
        assert forbidden not in lowered
