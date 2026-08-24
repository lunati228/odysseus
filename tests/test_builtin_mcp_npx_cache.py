import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_builtin_mcp(monkeypatch):
    core = types.ModuleType("core")
    core.__path__ = []
    platform_compat = types.ModuleType("core.platform_compat")
    platform_compat.IS_WINDOWS = False
    platform_compat.which_tool = lambda name: None
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.platform_compat", platform_compat)

    spec = importlib.util.spec_from_file_location(
        "builtin_mcp_under_test",
        ROOT / "src" / "builtin_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_npx_package_from_args_prefers_package_after_y_flag(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    assert builtin_mcp._npx_package_from_args(
        ["-y", "@playwright/mcp@latest", "--headless"]
    ) == "@playwright/mcp@latest"


def test_builtin_browser_package_is_exactly_pinned(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._BUILTIN_NPX_SERVERS["builtin_browser"]["args"]
    package_spec = builtin_mcp._npx_package_from_args(args)

    assert package_spec == "@playwright/mcp@0.0.78"
    assert "@latest" not in " ".join(args)


def test_cache_only_browser_launch_bypasses_npx(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    cli = tmp_path / "node_modules" / "@playwright" / "mcp" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// test fixture", encoding="utf-8")

    monkeypatch.setattr(builtin_mcp, "BROWSER_MCP_REQUIRE_CACHE", True)
    monkeypatch.setattr(
        builtin_mcp,
        "_find_cached_npx_package_bin",
        lambda package_spec: str(cli),
    )
    monkeypatch.setattr(builtin_mcp, "_find_node", lambda: "/usr/bin/node")

    command, args = builtin_mcp._browser_mcp_launch_command(
        "/usr/bin/npx",
        ["-y", "@playwright/mcp@0.0.78", "--headless", "--isolated"],
    )

    assert command == "/usr/bin/node"
    assert args == [str(cli), "--headless", "--isolated"]
    assert "/usr/bin/npx" not in [command, *args]


def test_cached_browser_launcher_requires_the_exact_package_version(
    monkeypatch,
    tmp_path,
):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    package_dir = (
        tmp_path / "_npx" / "fixture" / "node_modules" / "@playwright" / "mcp"
    )
    package_dir.mkdir(parents=True)
    (package_dir / "cli.js").write_text("// test fixture", encoding="utf-8")
    package_json = package_dir / "package.json"
    monkeypatch.setenv("npm_config_cache", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    package_json.write_text(
        json.dumps({
            "name": "@playwright/mcp",
            "version": "0.0.77",
            "bin": {"playwright-mcp": "cli.js"},
        }),
        encoding="utf-8",
    )
    assert builtin_mcp._find_cached_npx_package_bin(
        "@playwright/mcp@0.0.78"
    ) == ""

    package_json.write_text(
        json.dumps({
            "name": "@playwright/mcp",
            "version": "0.0.78",
            "bin": {"playwright-mcp": "cli.js"},
        }),
        encoding="utf-8",
    )
    assert builtin_mcp._find_cached_npx_package_bin(
        "@playwright/mcp@0.0.78"
    ) == str(package_dir / "cli.js")


def test_cached_browser_launcher_rejects_bin_path_escape(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    package_dir = (
        tmp_path / "_npx" / "fixture" / "node_modules" / "@playwright" / "mcp"
    )
    package_dir.mkdir(parents=True)
    outside = package_dir.parent / "outside.js"
    outside.write_text("// outside package", encoding="utf-8")
    (package_dir / "package.json").write_text(
        json.dumps({
            "name": "@playwright/mcp",
            "version": "0.0.78",
            "bin": {"playwright-mcp": "../outside.js"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("npm_config_cache", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert builtin_mcp._find_cached_npx_package_bin(
        "@playwright/mcp@0.0.78"
    ) == ""


def test_npx_package_name_parses_scoped_package_with_version(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    assert builtin_mcp._npx_package_name("@playwright/mcp@latest") == "@playwright/mcp"
    assert builtin_mcp._npx_package_name("@playwright/mcp") == "@playwright/mcp"
    assert builtin_mcp._npx_package_name("playwright@1.2.3") == "playwright"
    assert builtin_mcp._npx_package_name("") == ""


def test_browser_mcp_cache_requirement_is_opt_in(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_BROWSER_MCP_REQUIRE_CACHE", raising=False)
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    assert builtin_mcp.BROWSER_MCP_REQUIRE_CACHE is False


def test_browser_mcp_cache_requirement_can_be_enabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_REQUIRE_CACHE", "1")
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    assert builtin_mcp.BROWSER_MCP_REQUIRE_CACHE is True


def test_browser_mcp_args_use_configured_browser_executable(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_BROWSER_EXECUTABLE", "/usr/bin/chromium")
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])

    assert "--executable-path" in args
    assert "/usr/bin/chromium" in args
    assert "--isolated" in args
    assert "--no-sandbox" in args


def test_browser_mcp_args_can_use_persistent_profile_when_requested(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_BROWSER_EXECUTABLE", "/usr/bin/chromium")
    monkeypatch.setenv("ODYSSEUS_BROWSER_ISOLATED", "0")
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])

    assert "--executable-path" in args
    assert "--isolated" not in args


def test_browser_mcp_args_respect_explicit_user_data_dir(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_BROWSER_EXECUTABLE", "/usr/bin/chromium")
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._browser_mcp_args([
        "-y", "@playwright/mcp@latest", "--headless", "--user-data-dir", "/tmp/profile",
    ])

    assert "--user-data-dir" in args
    assert "--isolated" not in args


def test_browser_mcp_args_can_keep_sandbox(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_BROWSER_EXECUTABLE", "/usr/bin/chromium")
    monkeypatch.setenv("ODYSSEUS_BROWSER_NO_SANDBOX", "0")
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])

    assert "--executable-path" in args
    assert "--no-sandbox" not in args


def _write_fail_closed_browser_config(
    tmp_path,
    proxy_server,
    *,
    username=None,
    password=None,
):
    proxy_host = urlsplit(proxy_server).hostname
    resolver_exclusions = [proxy_host]
    if proxy_host != "127.0.0.1":
        resolver_exclusions.append("127.0.0.1")
    resolver_rule = "--host-resolver-rules=MAP * ~NOTFOUND, " + ", ".join(
        f"EXCLUDE {host}" for host in resolver_exclusions
    )
    proxy = {
        "server": proxy_server,
        "bypass": "localhost,127.0.0.1",
    }
    if username is not None:
        proxy["username"] = username
    if password is not None:
        proxy["password"] = password
    path = tmp_path / "browser-fallback.playwright.json"
    path.write_text(
        json.dumps({
            "browser": {
                "browserName": "chromium",
                "isolated": True,
                "launchOptions": {
                    "proxy": proxy,
                    "args": [
                        f"--proxy-server={proxy_server}",
                        resolver_rule,
                        "--dns-prefetch-disable",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    ],
                },
                "contextOptions": {
                    "proxy": proxy,
                    "serviceWorkers": "block",
                },
            },
        }),
        encoding="utf-8",
    )
    return path


def test_browser_mcp_args_use_validated_fail_closed_loopback_socks(monkeypatch, tmp_path):
    proxy = "socks5://127.0.0.1:10473"
    config = _write_fail_closed_browser_config(tmp_path, proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])

    assert "--proxy-server" not in args
    assert "--proxy-bypass" not in args
    assert args[args.index("--config") + 1] == str(config)
    assert "direct://" not in " ".join(args).lower()


def test_browser_mcp_args_use_authenticated_http_gateway_without_cli_secret_leak(
    monkeypatch,
    tmp_path,
):
    proxy = "http://192.168.1.25:10473"
    username = "gateway-user"
    password = "gateway-password"
    config = _write_fail_closed_browser_config(
        tmp_path,
        proxy,
        username=username,
        password=password,
    )
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_USERNAME", username)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_PASSWORD", password)
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    args = builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])

    rendered = " ".join(args)
    assert "--proxy-server" not in args
    assert "--proxy-bypass" not in args
    assert username not in rendered
    assert password not in rendered
    assert args[args.index("--config") + 1] == str(config)


def test_browser_mcp_rejects_dns_guard_that_blocks_the_lan_proxy_host(
    monkeypatch,
    tmp_path,
):
    proxy = "http://192.168.1.25:10473"
    config = _write_fail_closed_browser_config(
        tmp_path,
        proxy,
        username="gateway-user",
        password="gateway-password",
    )
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["browser"]["launchOptions"]["args"] = [
        arg
        for arg in payload["browser"]["launchOptions"]["args"]
        if not arg.startswith("--host-resolver-rules=")
    ] + ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
    config.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_USERNAME", "gateway-user")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_PASSWORD", "gateway-password")
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError, match="security guards"):
        builtin_mcp._browser_mcp_args(
            ["-y", "@playwright/mcp@latest", "--headless"]
        )


@pytest.mark.parametrize(
    ("proxy", "username", "password"),
    [
        ("http://192.168.1.25:10473", "", ""),
        ("http://192.168.1.25:10473", "gateway-user", ""),
        ("socks5://192.168.1.25:10473", "gateway-user", "gateway-password"),
    ],
)
def test_browser_mcp_rejects_unauthenticated_or_socks_lan_gateway(
    monkeypatch,
    tmp_path,
    proxy,
    username,
    password,
):
    config = _write_fail_closed_browser_config(
        tmp_path,
        proxy,
        username=username or None,
        password=password or None,
    )
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_USERNAME", username)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_PASSWORD", password)
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError, match="authenticated HTTP"):
        builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])


def test_browser_mcp_rejects_mismatched_proxy_credentials(monkeypatch, tmp_path):
    proxy = "http://192.168.1.25:10473"
    config = _write_fail_closed_browser_config(
        tmp_path,
        proxy,
        username="gateway-user",
        password="wrong-password",
    )
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_USERNAME", "gateway-user")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_PASSWORD", "gateway-password")
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError, match="does not match"):
        builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])


def test_browser_mcp_rejects_cli_proxy_override_of_validated_config(monkeypatch, tmp_path):
    proxy = "http://192.168.1.25:10473"
    config = _write_fail_closed_browser_config(
        tmp_path,
        proxy,
        username="gateway-user",
        password="gateway-password",
    )
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_USERNAME", "gateway-user")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_PASSWORD", "gateway-password")
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError, match="validated config"):
        builtin_mcp._browser_mcp_args([
            "-y",
            "@playwright/mcp@latest",
            "--headless",
            "--proxy-server",
            "http://127.0.0.1:9999",
        ])


@pytest.mark.parametrize(
    "proxy",
    [
        "https://127.0.0.1:1080",
        "socks5://example.com:1080",
        "socks5://user:secret@127.0.0.1:1080",
        "socks5://127.0.0.1:1080/path",
        "socks5://127.0.0.1",
        "socks5://127.0.0.1:1080,direct://",
    ],
)
def test_browser_mcp_rejects_nonlocal_or_fallthrough_proxy(monkeypatch, tmp_path, proxy):
    config = _write_fail_closed_browser_config(tmp_path, proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(config))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError):
        builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])


def test_browser_mcp_required_proxy_disables_direct_fallback(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.delenv("ODYSSEUS_BROWSER_PROXY_SERVER", raising=False)
    monkeypatch.delenv("ODYSSEUS_BROWSER_MCP_CONFIG", raising=False)
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError, match="required"):
        builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])


def test_browser_mcp_rejects_config_without_dns_and_webrtc_guards(monkeypatch, tmp_path):
    proxy = "socks5://127.0.0.1:1080"
    path = tmp_path / "unsafe.json"
    path.write_text(
        json.dumps({"browser": {"launchOptions": {"args": [f"--proxy-server={proxy}"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ODYSSEUS_BROWSER_REQUIRE_PROXY", "1")
    monkeypatch.setenv("ODYSSEUS_BROWSER_PROXY_SERVER", proxy)
    monkeypatch.setenv("ODYSSEUS_BROWSER_MCP_CONFIG", str(path))
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    with pytest.raises(ValueError, match="security guards"):
        builtin_mcp._browser_mcp_args(["-y", "@playwright/mcp@latest", "--headless"])


def test_npx_cache_check_detects_scoped_package_in_npx_cache(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    package_json = (
        tmp_path
        / ".npm"
        / "_npx"
        / "9833c18b2d85bc59"
        / "node_modules"
        / "@playwright"
        / "mcp"
        / "package.json"
    )
    package_json.parent.mkdir(parents=True)
    package_json.write_text('{"name":"@playwright/mcp","version":"0.0.76"}', encoding="utf-8")

    async def unexpected_exec(*args, **kwargs):
        raise AssertionError("cache hit should not shell out to npx")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("npm_config_cache", str(tmp_path / ".npm"))
    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unexpected_exec)

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx",
            "@playwright/mcp@latest",
            timeout_s=2,
        )
    ) is True


def test_npx_cache_check_falls_back_when_async_subprocess_is_unsupported(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    async def unsupported_exec(*args, **kwargs):
        raise NotImplementedError("subprocess transport unavailable")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout=b"1.2.3\n", stderr=b"")

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unsupported_exec)
    monkeypatch.setattr(builtin_mcp.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "empty-npm-cache"))

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx.cmd",
            "@playwright/mcp@latest",
            timeout_s=2,
        )
    ) is True
    assert captured["args"] == [
        "npx.cmd",
        "--no-install",
        "@playwright/mcp@latest",
        "--version",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["timeout"] == 2


def test_npx_cache_check_fallback_treats_timeout_as_cache_miss(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    async def unsupported_exec(*args, **kwargs):
        raise NotImplementedError("subprocess transport unavailable")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unsupported_exec)
    monkeypatch.setattr(builtin_mcp.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "empty-npm-cache"))

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx.cmd",
            "@playwright/mcp@latest",
            timeout_s=2,
        )
    ) is False
