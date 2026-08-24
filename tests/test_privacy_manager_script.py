from __future__ import annotations

import json
import os
import re
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
REGISTRY = Path(
    os.environ.get(
        "ODYSSEUS_MODEL_REGISTRY_TEST_PATH",
        PRIVATE_HOME / "config" / "models.json",
    )
)
BROWSER_FALLBACK = PRIVATE_HOME / "privacy-vault" / "browser-fallback.json"

_backup_candidates = sorted(
    (PRIVATE_HOME / "bin" / "backups").glob(
        "*.pre-privacy-workspace.*.ps1"
    )
)
BACKUP = Path(
    os.environ.get(
        "ODYSSEUS_MANAGER_BACKUP_TEST_PATH",
        _backup_candidates[-1]
        if _backup_candidates
        else PRIVATE_HOME / "bin" / "backups" / "pre-privacy-workspace.ps1",
    )
)

pytestmark = pytest.mark.skipif(
    not MANAGER.is_file() or not REGISTRY.is_file(),
    reason="the external Odysseus-Private manager installation is not present",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^function\s+{re.escape(name)}\b.*?(?=^function\s+|^switch\s*\(|\Z)",
        source,
    )
    assert match is not None, f"missing PowerShell function {name}"
    return match.group(0).rstrip()


def test_standard_7000_paths_and_actions_remain_source_identical_to_backup():
    if not BACKUP.is_file():
        pytest.skip("the pre-privacy manager backup is not configured")

    source = _source(MANAGER)
    backup = _source(BACKUP)

    for name in (
        "Stop-OwnedProcess",
        "Start-App",
        "Sync-Endpoint",
    ):
        assert _function(source, name) == _function(backup, name)

    standard_environment = _function(source, "Set-AppEnvironment")
    assert standard_environment.replace("\n    Set-BrowserEnvironment", "") == _function(
        backup, "Set-AppEnvironment"
    )

    for arm in (
        '"start-app" { Start-App }',
        '"start-model" { Start-Model }',
        '"sync-endpoint" { Sync-Endpoint }',
    ):
        assert arm in source
    assert re.search(r'"stop-app"\s*\{\s*Stop-OwnedProcess \$AppStatePath', source)
    assert re.search(r'"stop-model"\s*\{\s*Stop-OwnedProcess \$ModelStatePath', source)

    assert '$AppUrl = "http://127.0.0.1:7000"' in source
    assert '$env:APP_PORT = "7000"' in _function(source, "Set-AppEnvironment")
    assert '"$AppUrl/api/health"' in _function(source, "Start-App")


def test_model_manager_defaults_to_qwen_and_keeps_only_small_gemma_fallback():
    source = _source(MANAGER)
    parameter_block = source[:source.index("$ErrorActionPreference")]
    registry = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))

    assert '[ValidateSet("qwen", "gemma12")]' in parameter_block
    assert '[string]$Model = "qwen"' in parameter_block
    assert '"kat"' not in parameter_block
    assert '"gemma31"' not in parameter_block

    runtime = Path(registry["server"]["runtime"])
    assert runtime.is_absolute()
    assert runtime.name == "llama-server.exe"
    assert [model["key"] for model in registry["models"]] == ["qwen", "gemma12"]

    qwen = registry["models"][0]
    assert isinstance(qwen["alias"], str) and qwen["alias"]
    assert Path(qwen["model"]).suffix.lower() == ".gguf"
    assert qwen["modelBytes"] > 0
    assert re.fullmatch(r"[0-9a-fA-F]{64}", qwen["modelSha256"])
    assert Path(qwen["mmproj"]).suffix.lower() == ".gguf"
    assert qwen["mmprojBytes"] > 0
    assert qwen["mtpDefault"] is True
    assert qwen["mtpMode"] == "native"

    args = qwen["args"]
    predict_at = args.index("--n-predict")
    assert args[predict_at + 1] == "-1"
    reasoning_at = args.index("--reasoning-effort")
    assert args[reasoning_at + 1] in {"low", "medium", "xhigh"}


def test_start_model_uses_qwen_native_mtp_n2_without_a_separate_drafter():
    start_model = _function(_source(MANAGER), "Start-Model")

    assert '$mtpMode = [string]$spec.mtpMode' in start_model
    assert '$mtpMode -eq "native"' in start_model
    assert '"--spec-type", "draft-mtp", "--spec-draft-n-max", "2"' in start_model
    assert '$mtpMode -eq "external"' in start_model


def test_start_model_avoids_windows_mmap_primes_chat_graph_and_fails_safe_on_low_ram():
    source = _source(MANAGER)
    start_model = _function(source, "Start-Model")
    memory_guard = _function(source, "Assert-ModelMemorySafe")
    warmup = _function(source, "Invoke-ModelWarmup")

    assert "Add-Type -AssemblyName System.Net.Http" in source[: source.index("function Write-JsonNoBom")]
    assert '"--load-mode", "none"' in start_model
    assert '"--load-mode", "mmap"' not in start_model
    assert "Assert-ModelMemorySafe $process" in start_model
    assert "Invoke-ModelWarmup $process $spec.alias" in start_model

    assert "Get-AvailableMemoryMiB" in memory_guard
    assert "$ModelMinimumFreeMemoryMiB" in memory_guard
    assert "Stop-Process" in memory_guard
    assert "Remove-Item -LiteralPath $ModelStatePath" in memory_guard
    assert "Safety cutoff" in memory_guard

    assert '"$ModelUrl/v1/chat/completions"' in warmup
    assert 'content = "Warm up the local inference graph. Reply with one token."' in warmup
    assert "max_tokens = 1" in warmup
    assert "cache_prompt = $false" in warmup
    assert "Assert-ModelMemorySafe $Process" in warmup
    assert "CredentialPath" not in warmup


def test_browser_fallback_uses_authenticated_brave_windscribe_and_fails_closed():
    source = _source(MANAGER)
    browser_environment = _function(source, "Set-BrowserEnvironment")

    assert BROWSER_FALLBACK.is_file()
    assert re.search(r'(?mi)^\$BraveExe\s*=\s*".+brave\.exe"$', source)
    assert '$BrowserFallbackPath = Join-Path $PrivacyVault "browser-fallback.json"' in source
    assert '$BrowserPlaywrightConfig = Join-Path $PrivacyRunDir "browser-fallback.playwright.json"' in source
    assert "Set-BrowserEnvironment" in _function(source, "Set-AppEnvironment")
    assert "Set-BrowserEnvironment" in _function(source, "Set-PrivacyAppEnvironment")

    for assignment in (
        '$env:ODYSSEUS_BROWSER_REQUIRE_PROXY = "1"',
        '$env:ODYSSEUS_BROWSER_ROLE = "windscribe-fallback"',
        '$env:ODYSSEUS_BROWSER_NO_SANDBOX = "0"',
        '$env:ODYSSEUS_BROWSER_ISOLATED = "1"',
        '$env:ODYSSEUS_BROWSER_MCP_REQUIRE_CACHE = "1"',
    ):
        assert assignment in browser_environment

    for guard in (
        "--proxy-server=",
        "--host-resolver-rules=$resolverRules",
        "--dns-prefetch-disable",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "serviceWorkers = \"block\"",
        "chromiumSandbox = $true",
    ):
        assert guard in browser_environment
    assert '$resolverHosts = @($uri.Host)' in browser_environment
    assert '$uri.Host -ne "127.0.0.1"' in browser_environment
    assert '"EXCLUDE $_"' in browser_environment
    assert '.Contains("direct://")' in browser_environment
    assert "forbids direct fallback" in browser_environment.lower()
    assert "authenticated HTTP" in browser_environment
    assert '$env:ODYSSEUS_BROWSER_PROXY_USERNAME = $proxyUsername' in browser_environment
    assert '$env:ODYSSEUS_BROWSER_PROXY_PASSWORD = $proxyPassword' in browser_environment
    assert "username = $proxyUsername" in browser_environment
    assert "password = $proxyPassword" in browser_environment
    assert "Get-AuthenticodeSignature" in browser_environment
    assert "Brave Software" in browser_environment

def test_actions_and_pinned_privacy_runtime_constants_are_declared():
    source = _source(MANAGER)

    for action in ("start-tor", "stop-tor", "start-private", "stop-private"):
        assert f'"{action}"' in source

    expected_literals = (
        '$TorSocksHost = "127.0.0.1"',
        '$TorSocksPort = 19050',
        '$TorSocksUrl = "socks5h://127.0.0.1:19050"',
        '$PrivacyAppUrl = "http://127.0.0.1:7001"',
    )
    for literal in expected_literals:
        assert literal in source

    for variable in ("TorExe", "TorExpectedBytes", "TorExpectedSha256", "Torrc"):
        assert re.search(rf"(?m)^\${variable}\s*=", source)
    assert re.search(r'(?mi)^\$TorExpectedSha256\s*=\s*"[0-9a-f]{64}"$', source)

    assert '$PrivacyVault = Join-Path $Root "privacy-vault"' in source
    assert '$TorStatePath = Join-Path $PrivacyRunDir "tor.json"' in source
    assert '$PrivacyAppStatePath = Join-Path $PrivacyRunDir "app.json"' in source


def test_start_tor_is_integrity_checked_verified_hidden_and_fail_closed():
    source = _source(MANAGER)
    start_tor = _function(source, "Start-Tor")

    assert "Assert-FileIdentity $TorExe $TorExpectedBytes $TorExpectedSha256" in start_tor
    assert '"--verify-config"' in start_tor
    assert "$verify.ExitCode -ne 0" in start_tor
    assert "Assert-PortFree $TorSocksPort" in start_tor
    assert "Start-Process" in start_tor
    assert "-WindowStyle Hidden" in start_tor
    assert "-RedirectStandardOutput" in start_tor
    assert "-RedirectStandardError" in start_tor
    assert "Get-LoopbackListenerPid $TorSocksPort" in start_tor
    assert "$listenerPid -eq $process.Id" in start_tor
    assert "Bootstrapped\\s+100%" in start_tor
    assert "Write-JsonNoBom $TorStatePath" in start_tor
    assert "throw" in start_tor


def test_new_stop_paths_bind_pid_to_executable_start_time_and_port():
    source = _source(MANAGER)
    verifier = _function(source, "Assert-OwnedProcessIdentity")

    assert ".Path.Equals(" in verifier
    assert "OrdinalIgnoreCase" in verifier
    assert "StartTime.ToUniversalTime().Ticks" in verifier
    assert "Get-LoopbackListenerPid" in verifier
    assert "listener" in verifier.lower()
    assert "refusing" in verifier.lower()

    stop_tor = _function(source, "Stop-Tor")
    assert "Assert-OwnedProcessIdentity" in stop_tor
    assert "$TorExe" in stop_tor
    assert "$TorSocksPort" in stop_tor

    stop_private_app = _function(source, "Stop-PrivacyApp")
    assert "Assert-OwnedProcessIdentity" in stop_private_app
    assert "$PrivacyAppPort" in stop_private_app
    assert "$PrivacyAppStatePath" in stop_private_app
    assert "Stop-Process -Id $process.Id -Force -ErrorAction Stop" in stop_private_app
    assert "Get-Process -Id $process.Id -ErrorAction SilentlyContinue" in stop_private_app
    assert "catch [System.InvalidOperationException]" in stop_private_app


def test_private_child_environment_is_vault_confined_offline_and_proxy_clean():
    source = _source(MANAGER)
    environment = _function(source, "Set-PrivacyAppEnvironment")

    expected_assignments = (
        '$env:ODYSSEUS_PROFILE = "privacy"',
        '$env:ODYSSEUS_DATA_DIR = Join-Path $PrivacyVault "data"',
        '$env:APP_LOGS_DIR = $PrivacyLogsDir',
        '$env:APP_BIND = "127.0.0.1"',
        '$env:APP_PORT = "7001"',
        '$env:ODYSSEUS_INTERNAL_BASE = $PrivacyAppUrl',
        '$env:APP_PUBLIC_URL = $PrivacyAppUrl',
        '$env:OAUTH_REDIRECT_BASE_URL = $PrivacyAppUrl',
        '$env:ODYSSEUS_SESSION_COOKIE = "odysseus_privacy_session"',
        '$env:ODYSSEUS_TOR_SOCKS_URL = $TorSocksUrl',
        '$env:ODYSSEUS_INPROCESS_TASKS = "0"',
        '$env:ODYSSEUS_INPROCESS_POLLERS = "0"',
        '$env:ODYSSEUS_STARTUP_WARMUPS = "0"',
        '$env:ODYSSEUS_MODEL_KEEPALIVE = "0"',
        '$env:HF_HUB_DISABLE_TELEMETRY = "1"',
        '$env:DO_NOT_TRACK = "1"',
        '$env:ANONYMIZED_TELEMETRY = "FALSE"',
        '$env:HF_HUB_OFFLINE = "1"',
        '$env:TRANSFORMERS_OFFLINE = "1"',
        '$env:HF_DATASETS_OFFLINE = "1"',
        '$env:NO_PROXY = "127.0.0.1,localhost"',
    )
    for assignment in expected_assignments:
        assert assignment in environment
    assert re.search(r'(?m)^\s*\$env:DATABASE_URL\s*=\s*"sqlite:///', environment)

    for cache_name in (
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "SENTENCE_TRANSFORMERS_HOME",
        "FASTEMBED_CACHE_PATH",
        "XDG_CACHE_HOME",
        "TORCH_HOME",
    ):
        assert f"$env:{cache_name}" in environment
    assert "$PrivacyOfflineDir" in environment

    scrubber = _function(source, "Clear-PrivateChildSecretsAndProxies")
    for proxy_name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        assert f'"{proxy_name}"' in scrubber
    for key_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "SERPER_API_KEY",
        "OPENROUTER_API_KEY",
        "HF_TOKEN",
        "HUGGINGFACE_HUB_TOKEN",
    ):
        assert f'"{key_name}"' in scrubber
    assert 'SetEnvironmentVariable($name, "", [EnvironmentVariableTarget]::Process)' in scrubber


def test_private_start_requires_owned_bootstrapped_tor_and_uses_ready_probe():
    source = _source(MANAGER)
    start_private = _function(source, "Start-Private")
    start_app = _function(source, "Start-PrivacyApp")

    assert "Start-Tor" in start_private
    assert "Assert-TorReady" in start_private
    assert "Start-PrivacyApp" in start_private
    assert start_private.index("Assert-TorReady") < start_private.index("Start-PrivacyApp")
    assert "Sync-Endpoint" not in start_private
    assert "CredentialPath" not in start_private

    assert '"$PrivacyAppUrl/api/ready"' in start_app
    assert "/api/health" not in start_app
    assert "Assert-PortFree $PrivacyAppPort" in start_app
    assert "-WindowStyle Hidden" in start_app
    assert "Write-JsonNoBom $PrivacyAppStatePath" in start_app


def test_status_adds_non_secret_private_runtime_fields():
    status = _function(_source(MANAGER), "Show-Status")

    for field in (
        "torRunning",
        "torPid",
        "torSocksUrl",
        "privateAppRunning",
        "privateAppPid",
        "privateAppUrl",
    ):
        assert re.search(rf"(?m)^\s*{field}\s*=", status)

    for forbidden_field in (
        "credential",
        "password",
        "apiKey",
        "api_key",
        "cookie",
        "stdout",
        "stderr",
    ):
        assert not re.search(rf"(?mi)^\s*{forbidden_field}\s*=", status)
