"""
builtin_mcp.py

Auto-registration of built-in MCP servers on startup.
Each server runs as a stdio subprocess managed by McpManager.
"""

import asyncio
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from core.platform_compat import IS_WINDOWS, which_tool
from src.runtime_paths import get_app_root

logger = logging.getLogger(__name__)


def _find_npx() -> str:
    """Find the npx binary, checking common locations if not on PATH.

    On Windows the shim is `npx.cmd`, which `which_tool` resolves via PATHEXT.
    """
    npx = which_tool("npx")
    if npx:
        return npx
    if IS_WINDOWS:
        # Minimal-PATH fallbacks: npm's global bin lives under %APPDATA%\npm,
        # and node's installer dir carries npx.cmd alongside node.exe.
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        for candidate in (
            os.path.join(appdata, "npm", "npx.cmd"),
            r"C:\Program Files\nodejs\npx.cmd",
        ):
            if os.path.isfile(candidate):
                return candidate
        node = which_tool("node")
        if node:
            cand = os.path.join(os.path.dirname(node), "npx.cmd")
            if os.path.isfile(cand):
                return cand
        return "npx.cmd"  # fallback, will fail with a clear error
    # Common POSIX locations when PATH is minimal (e.g. systemd)
    for candidate in [
        os.path.expanduser("~/.npm-global/bin/npx"),
        os.path.expanduser("~/.local/bin/npx"),
        "/usr/local/bin/npx",
        "/usr/bin/npx",
    ]:
        if os.path.isfile(candidate):
            return candidate
    # Try to find node and use npx from same dir
    node = shutil.which("node")
    if node:
        npx_candidate = os.path.join(os.path.dirname(node), "npx")
        if os.path.isfile(npx_candidate):
            return npx_candidate
    return "npx"  # fallback, will fail with a clear error


def _find_node() -> str:
    """Find the Node.js runtime used to execute a verified cached MCP CLI."""
    node = which_tool("node") or shutil.which("node")
    if node:
        return node
    if IS_WINDOWS:
        candidate = r"C:\Program Files\nodejs\node.exe"
        if os.path.isfile(candidate):
            return candidate
        return "node.exe"
    return "node"


# Server definitions: id -> (script path relative to project root, display name)
#
# bash / python / filesystem / web_search were folded into native in-process
# execution (src/tool_execution.py:_direct_fallback). Those trivial subprocess
# wrappers are gone.
#
# image_gen / memory / rag / email still run as stdio MCP servers — each
# carries hundreds of LOC of unique IMAP / HTTP / manager logic not worth
# duplicating into the native path right now.
_BUILTIN_SERVERS = {
    "image_gen":  ("mcp_servers/image_gen_server.py",  "Built-in: Image Generation"),
    "memory":     ("mcp_servers/memory_server.py",     "Built-in: Memory"),
    "rag":        ("mcp_servers/rag_server.py",        "Built-in: RAG"),
    "email":      ("mcp_servers/email_server.py",      "Built-in: Email"),
}

# NPX-based built-in servers (run via npx, not Python)
_BUILTIN_NPX_SERVERS = {
    "builtin_browser": {
        "name": "Built-in: Browser",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@0.0.78", "--headless", "--caps", "vision"],
    }
}

# Global flag to disable MCP if there are compatibility issues
MCP_DISABLED = os.environ.get("ODYSSEUS_DISABLE_MCP", "").lower() in ("1", "true", "yes")
BROWSER_MCP_REQUIRE_CACHE = os.environ.get("ODYSSEUS_BROWSER_MCP_REQUIRE_CACHE", "").lower() in ("1", "true", "yes")


# Strong references to the fire-and-forget startup tasks scheduled below.
# asyncio only keeps weak references to tasks created via create_task, so
# without this the GC can collect a task mid-execution and the server
# registration silently never runs. Mirrors _spawn_bg in routes/chat_helpers.py.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> asyncio.Task:
    """Schedule a background task and hold a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task

def _find_browser_executable() -> str:
    """Find a browser binary for the built-in Playwright MCP server.

    Docker images ship Debian's `chromium`; desktop installs may already have
    Chrome/Chromium in a conventional location. If nothing is found, return an
    empty string and let Playwright MCP use its own default browser/channel.
    """
    configured = os.environ.get("ODYSSEUS_BROWSER_EXECUTABLE", "").strip()
    if configured:
        return configured
    for name in ("google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in (
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if os.path.isfile(candidate):
            return candidate
    return ""


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _validated_browser_proxy() -> tuple[str, str, str]:
    """Return a validated endpoint and out-of-URL credentials, or fail closed.

    Windscribe Proxy Gateway is LAN-facing. A non-loopback listener therefore
    requires authenticated HTTP so another LAN client cannot silently consume
    the user's VPN allowance. Playwright documents username/password support
    for HTTP(S), not SOCKS5: https://playwright.dev/docs/network#http-proxy
    """
    raw = os.environ.get("ODYSSEUS_BROWSER_PROXY_SERVER", "").strip()
    username = os.environ.get("ODYSSEUS_BROWSER_PROXY_USERNAME", "").strip()
    password = os.environ.get("ODYSSEUS_BROWSER_PROXY_PASSWORD", "")
    required = _env_enabled("ODYSSEUS_BROWSER_REQUIRE_PROXY")
    if not raw:
        if required:
            raise ValueError("A browser proxy is required; direct browser fallback is disabled")
        if username or password:
            raise ValueError("Browser proxy credentials require a proxy endpoint")
        return "", "", ""
    if "direct://" in raw.lower():
        raise ValueError("Direct browser fallback is forbidden")

    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Browser proxy endpoint is malformed") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "socks5"):
        raise ValueError("Browser fallback requires HTTP or loopback SOCKS5")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Browser proxy credentials must not be embedded in the endpoint URL")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Browser proxy must not contain a path, query, or fragment")
    if not parsed.hostname or port is None or not 1 <= port <= 65535:
        raise ValueError("Browser proxy requires an explicit valid port")

    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("Browser proxy host must be a numeric local address") from exc
    private_lan = any(address in network for network in (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    ))
    if address.version != 4 or not (address.is_loopback or private_lan):
        raise ValueError("Browser proxy host must be loopback or private IPv4")
    has_username = bool(username)
    has_password = bool(password)
    if has_username != has_password:
        raise ValueError("A private-LAN fallback requires authenticated HTTP credentials")
    if scheme == "socks5" and (not address.is_loopback or has_username):
        raise ValueError("A private-LAN fallback requires authenticated HTTP, not SOCKS5")
    if private_lan and not address.is_loopback and not (has_username and has_password):
        raise ValueError("A private-LAN fallback requires authenticated HTTP credentials")
    return raw, username, password


def _validated_browser_mcp_config(
    config_path: str,
    proxy_server: str,
    proxy_username: str,
    proxy_password: str,
) -> str:
    """Require the launch-level guards that prevent proxy and side-channel leaks."""
    if not config_path:
        raise ValueError("A fail-closed browser MCP config is required with the proxy")
    if not os.path.isabs(config_path) or not os.path.isfile(config_path):
        raise ValueError("Browser MCP config must be an existing absolute file")

    try:
        with open(config_path, encoding="utf-8-sig") as handle:
            config = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Browser MCP config is unreadable or invalid JSON") from exc

    browser = config.get("browser") if isinstance(config, dict) else None
    launch = browser.get("launchOptions") if isinstance(browser, dict) else None
    context = browser.get("contextOptions") if isinstance(browser, dict) else None
    launch_args = launch.get("args") if isinstance(launch, dict) else None
    launch_proxy = launch.get("proxy") if isinstance(launch, dict) else None
    context_proxy = context.get("proxy") if isinstance(context, dict) else None
    if not isinstance(launch_args, list) or not all(isinstance(arg, str) for arg in launch_args):
        raise ValueError("Browser MCP config is missing launch security guards")

    proxy_host = urlsplit(proxy_server).hostname
    resolver_hosts = [proxy_host]
    if proxy_host != "127.0.0.1":
        resolver_hosts.append("127.0.0.1")
    resolver_guard = "--host-resolver-rules=MAP * ~NOTFOUND, " + ", ".join(
        f"EXCLUDE {host}" for host in resolver_hosts
    )
    required_args = {
        f"--proxy-server={proxy_server}",
        resolver_guard,
        "--dns-prefetch-disable",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    }
    if not required_args.issubset(set(launch_args)):
        raise ValueError("Browser MCP config is missing launch security guards")
    resolver_args = [
        arg for arg in launch_args if arg.startswith("--host-resolver-rules=")
    ]
    if resolver_args != [resolver_guard]:
        raise ValueError("Browser MCP config is missing launch security guards")
    if any("direct://" in arg.lower() for arg in launch_args):
        raise ValueError("Browser MCP config permits a forbidden direct fallback")
    if browser.get("isolated") is not True:
        raise ValueError("Browser MCP fallback must use an isolated profile")
    expected_proxy = {
        "server": proxy_server,
        "bypass": "localhost,127.0.0.1",
    }
    if proxy_username:
        expected_proxy["username"] = proxy_username
        expected_proxy["password"] = proxy_password
    if launch_proxy != expected_proxy or context_proxy != expected_proxy:
        raise ValueError("Browser MCP proxy config does not match the validated settings")
    if context.get("serviceWorkers") != "block":
        raise ValueError("Browser MCP fallback must block service workers")
    return config_path


def _browser_mcp_args(args: list[str]) -> list[str]:
    """Return Playwright MCP args with an optional fail-closed browser proxy."""
    out = list(args or [])
    if "--executable-path" not in out:
        browser = _find_browser_executable()
        if browser:
            out.extend(["--executable-path", browser])
    if os.environ.get("ODYSSEUS_BROWSER_ISOLATED", "1").lower() not in ("0", "false", "no"):
        if "--isolated" not in out and "--user-data-dir" not in out:
            out.append("--isolated")
    proxy_server, proxy_username, proxy_password = _validated_browser_proxy()
    if proxy_server:
        if any(
            arg in ("--proxy-server", "--proxy-bypass")
            or arg.startswith("--proxy-server=")
            or arg.startswith("--proxy-bypass=")
            for arg in out
        ):
            raise ValueError("Browser proxy settings must come only from the validated config")
        config_path = _validated_browser_mcp_config(
            os.environ.get("ODYSSEUS_BROWSER_MCP_CONFIG", "").strip(),
            proxy_server,
            proxy_username,
            proxy_password,
        )
        if "--no-sandbox" in out:
            raise ValueError("The managed browser fallback cannot disable the browser sandbox")
        if "--sandbox" not in out:
            out.append("--sandbox")
        out.extend([
            "--block-service-workers",
            "--config", config_path,
        ])
    elif os.environ.get("ODYSSEUS_BROWSER_NO_SANDBOX", "1").lower() not in ("0", "false", "no"):
        if "--no-sandbox" not in out and "--sandbox" not in out:
            out.append("--no-sandbox")
    return out


def _browser_mcp_launch_command(
    npx_path: str,
    args: list[str],
) -> tuple[str, list[str]]:
    """Use npx normally, or execute an exact cached CLI without npm access."""
    if not BROWSER_MCP_REQUIRE_CACHE:
        return npx_path, list(args)

    package_spec = _npx_package_from_args(args)
    launcher = _find_cached_npx_package_bin(package_spec)
    if not package_spec or not launcher:
        raise FileNotFoundError(
            f"exact cached npm package {package_spec or '<missing>'!r} was not found"
        )
    package_index = args.index(package_spec)
    return _find_node(), [launcher, *args[package_index + 1:]]


def builtin_python_env(base_dir: str) -> dict[str, str]:
    """Environment for built-in Python MCP subprocesses.

    The app root must be importable so mcp_servers can import local modules, but
    replacing PYTHONPATH entirely hides site-packages in container/dev launches
    that rely on PYTHONPATH for their active environment.
    """
    existing = os.environ.get("PYTHONPATH", "")
    parts = [base_dir]
    for item in existing.split(os.pathsep):
        if item and item not in parts:
            parts.append(item)
    return {"PYTHONPATH": os.pathsep.join(parts)}


async def register_builtin_servers(mcp_manager, *, only_browser: bool = False):
    """Connect built-in MCP servers to the manager.

    "only_browser" restricts registration to the built-in Playwright/Brave
    browser server and is used by Privacy Workspace so user-configured MCP
    servers (and the email/memory/RAG/image Python servers) never start there.
    """
    if MCP_DISABLED:
        logger.info("Built-in MCP servers disabled via ODYSSEUS_DISABLE_MCP")
        return

    base_dir = get_app_root()
    python = sys.executable

    async def _connect_python_server(server_id: str, script_path: str, name: str):
        try:
            ok = await mcp_manager.connect_server(
                server_id=server_id,
                name=name,
                transport="stdio",
                command=python,
                args=[script_path],
                env=builtin_python_env(base_dir),
            )
            if ok:
                logger.info(f"Built-in MCP server registered: {name}")
            else:
                logger.warning(f"Built-in MCP server failed to connect: {name}")
        except asyncio.CancelledError:
            logger.warning(f"Built-in MCP server {name} cancelled")
            raise
        except BaseException as e:
            logger.warning(f"Built-in MCP server {name} error: {type(e).__name__}: {e}")

    if not only_browser:
        for server_id, (script, name) in _BUILTIN_SERVERS.items():
            script_path = os.path.join(base_dir, script)
            if not os.path.exists(script_path):
                logger.warning(f"Built-in MCP server script not found: {script_path}")
                continue
            _spawn_bg(_connect_python_server(server_id, script_path, name))

    # Register NPX-based servers in the background (they take longer to start)
    npx_path = _find_npx()
    logger.info(f"NPX binary resolved to: {npx_path}")

    async def _start_npx_servers():
        await asyncio.sleep(3)  # let Python servers finish first
        for server_id, cfg in _BUILTIN_NPX_SERVERS.items():
            try:
                args = _browser_mcp_args(cfg["args"]) if server_id == "builtin_browser" else list(cfg["args"])
                command, args = (
                    _browser_mcp_launch_command(npx_path, args)
                    if server_id == "builtin_browser"
                    else (npx_path, args)
                )
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("Built-in browser disabled: %s", exc)
                continue
            name = cfg["name"]
            if server_id == "builtin_browser" and os.environ.get("ODYSSEUS_BROWSER_ROLE") == "windscribe-fallback":
                name = "Built-in: Browser (Windscribe fallback)"

            logger.info(f"Starting browser MCP server: {name} ({command} {' '.join(args)})")
            try:
                env = None
                if server_id == "builtin_browser":
                    cache_home = os.environ.get(
                        "ODYSSEUS_BROWSER_MCP_CACHE",
                        os.path.join(base_dir, "data", "local", "playwright-mcp-cache"),
                    )
                    os.makedirs(cache_home, exist_ok=True)
                    env = {
                        "XDG_CACHE_HOME": cache_home,
                        "PLAYWRIGHT_BROWSERS_PATH": os.path.join(cache_home, "browsers"),
                    }
                ok = await mcp_manager.connect_server(
                    server_id=server_id,
                    name=name,
                    transport="stdio",
                    command=command,
                    args=args,
                    env=env,
                )
                if ok:
                    logger.info(f"Built-in NPX server registered: {name}")
                else:
                    logger.warning(f"Built-in NPX server failed to connect: {name}")
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                logger.warning(f"Built-in NPX server {name} error: {type(e).__name__}: {e}")

    _spawn_bg(_start_npx_servers())


def _npx_package_from_args(args):
    """Pick the package spec out of an npx args list shaped like
    ['-y', '<package@version>', ...flags]. Returns None if the
    convention doesn't match (we then skip the cache check and just
    try the connect)."""
    if not args:
        return None
    if "-y" in args:
        idx = args.index("-y") + 1
        if idx < len(args) and not args[idx].startswith("-"):
            return args[idx]
    # No -y prefix: first non-flag arg is the package
    for a in args:
        if not a.startswith("-"):
            return a
    return None


async def _is_npx_package_cached(npx_path, package_spec, timeout_s=5):
    """Probe whether an npx package is already in the local cache.

    First checks the local `_npx` cache for an installed package. If the
    package is not found there, falls back to `npx --no-install <pkg>
    --version` so older npm layouts still work without downloading.
    """
    if _is_package_in_npx_cache(package_spec):
        return True

    try:
        proc = await asyncio.create_subprocess_exec(
            npx_path, "--no-install", package_spec, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        try:
            result = subprocess.run(
                [npx_path, "--no-install", package_spec, "--version"],
                capture_output=True,
                timeout=timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, ValueError):
        return False
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return False
    except asyncio.CancelledError:
        # The probe was cancelled (e.g. app shutdown). Reap the child so it
        # isn't orphaned, then propagate the cancellation.
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise
    return proc.returncode == 0 and bool(stdout.strip())


def _is_package_in_npx_cache(package_spec):
    """Return True when npm's `_npx` cache already contains package_spec."""
    package_name = _npx_package_name(package_spec)
    if not package_name:
        return False

    for cache_root in _npm_cache_roots():
        npx_root = os.path.join(cache_root, "_npx")
        if _npx_cache_contains_package(npx_root, package_name):
            return True
    return False


def _npx_package_name(package_spec):
    """Strip a version/range suffix from an npm package spec."""
    if not package_spec:
        return ""
    if package_spec.startswith("@"):
        parts = package_spec.split("@")
        if len(parts) >= 3:
            return "@" + parts[1]
        return package_spec
    return package_spec.split("@", 1)[0]


def _find_cached_npx_package_bin(package_spec: str | None) -> str:
    """Resolve an exact-version package CLI inside npm's npx cache."""
    package_name = _npx_package_name(package_spec)
    version_prefix = f"{package_name}@" if package_name else ""
    if not version_prefix or not str(package_spec).startswith(version_prefix):
        return ""
    expected_version = str(package_spec)[len(version_prefix):]
    if not expected_version or expected_version == "latest":
        return ""

    relative_package = os.path.join("node_modules", *package_name.split("/"))
    for cache_root in _npm_cache_roots():
        npx_root = os.path.join(cache_root, "_npx")
        try:
            entries = list(os.scandir(npx_root))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            package_dir = os.path.join(entry.path, relative_package)
            package_json = os.path.join(package_dir, "package.json")
            try:
                with open(package_json, encoding="utf-8") as handle:
                    metadata = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(metadata, dict):
                continue
            if metadata.get("name") != package_name:
                continue
            if str(metadata.get("version", "")) != expected_version:
                continue

            bin_field = metadata.get("bin")
            if isinstance(bin_field, str):
                relative_bin = bin_field
            elif isinstance(bin_field, dict) and len(bin_field) == 1:
                relative_bin = next(iter(bin_field.values()))
            else:
                continue
            if not isinstance(relative_bin, str) or not relative_bin:
                continue

            package_real = os.path.realpath(package_dir)
            candidate = os.path.realpath(os.path.join(package_dir, relative_bin))
            try:
                inside_package = os.path.commonpath([package_real, candidate])
            except ValueError:
                continue
            if os.path.normcase(inside_package) != os.path.normcase(package_real):
                continue
            if os.path.isfile(candidate):
                return candidate
    return ""


def _npm_cache_roots():
    roots = []
    configured = os.environ.get("npm_config_cache")
    if configured:
        roots.append(os.path.expanduser(configured))
    roots.append(os.path.join(os.path.expanduser("~"), ".npm"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(os.path.join(local_app_data, "npm-cache"))
    return list(dict.fromkeys(roots))


def _npx_cache_contains_package(npx_root, package_name):
    if not os.path.isdir(npx_root):
        return False
    package_path = os.path.join("node_modules", *package_name.split("/"), "package.json")
    try:
        entries = list(os.scandir(npx_root))
    except OSError:
        return False
    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        cached_name = _cached_package_name(os.path.join(entry.path, package_path))
        if is_dir and cached_name == package_name:
            return True
    return False


def _cached_package_name(package_json_path):
    try:
        with open(package_json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ""
    return str(data.get("name", "")).strip()
