"""Import-time profile and isolation helpers.

This module deliberately depends only on the Python standard library.  It is
safe to import before dotenv, database, model, or application initialization.
"""
from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import unquote, urlsplit


class PrivacyConfigurationError(RuntimeError):
    """Raised when Privacy Mode configuration is not fail-closed."""


def normalize_profile(value: object) -> str:
    """Return the canonical process profile name."""
    raw = "" if value is None else str(value).strip().lower()
    if raw in {"", "standard", "normal"}:
        return "standard"
    if raw == "privacy":
        return "privacy"
    raise PrivacyConfigurationError(
        "ODYSSEUS_PROFILE must be one of: standard, normal, privacy"
    )


PROFILE = normalize_profile(os.environ.get("ODYSSEUS_PROFILE"))


def current_profile() -> str:
    return PROFILE


def is_privacy_mode(profile: Optional[str] = None) -> bool:
    selected = PROFILE if profile is None else normalize_profile(profile)
    return selected == "privacy"


def _resolved_absolute_path(raw: os.PathLike[str] | str, label: str) -> Path:
    if raw is None or not str(raw).strip():
        raise PrivacyConfigurationError(f"{label} is required in Privacy Mode")
    candidate = Path(os.path.expanduser(str(raw).strip()))
    if not candidate.is_absolute():
        raise PrivacyConfigurationError(f"{label} must be an absolute path")
    return candidate.resolve(strict=False)


def _is_within(candidate: Path, root: Path) -> bool:
    candidate_key = os.path.normcase(str(candidate))
    root_key = os.path.normcase(str(root))
    try:
        return os.path.commonpath((candidate_key, root_key)) == root_key
    except ValueError:
        return False


def confine_path(
    raw: os.PathLike[str] | str,
    data_root: os.PathLike[str] | str,
    label: str = "path",
) -> Path:
    """Resolve an absolute path and require it to stay under ``data_root``."""
    root = _resolved_absolute_path(data_root, "Privacy data root")
    candidate = _resolved_absolute_path(raw, label)
    if not _is_within(candidate, root):
        raise PrivacyConfigurationError(
            f"{label} must resolve inside the Privacy data root"
        )
    return candidate


def validate_privacy_data_root(
    raw: os.PathLike[str] | str | None,
    *,
    repo_root: os.PathLike[str] | str | None = None,
) -> Path:
    """Require an absolute data root that neither contains nor enters the repo."""
    data_root = _resolved_absolute_path(raw, "ODYSSEUS_DATA_DIR")
    if repo_root is None:
        from src.runtime_paths import get_app_root

        repo_root = get_app_root()
    repository = _resolved_absolute_path(repo_root, "Repository root")
    if _is_within(data_root, repository) or _is_within(repository, data_root):
        raise PrivacyConfigurationError(
            "ODYSSEUS_DATA_DIR must not overlap the application repository"
        )
    return data_root


_SQLITE_FILE_URL = re.compile(r"^sqlite(?:\+[A-Za-z0-9_.-]+)?:///", re.IGNORECASE)


def validate_database_url(
    url: str,
    *,
    data_root: os.PathLike[str] | str,
    profile: Optional[str] = None,
) -> str:
    """Validate and canonicalize Privacy Mode's file-backed SQLite URL."""
    if not is_privacy_mode(profile):
        return url
    raw = str(url or "").strip()
    match = _SQLITE_FILE_URL.match(raw)
    if not match or "?" in raw or "#" in raw:
        raise PrivacyConfigurationError(
            "Privacy Mode DATABASE_URL must be a plain file-backed SQLite URL"
        )
    path_text = unquote(raw[match.end() :])
    if not path_text or path_text == ":memory:" or path_text.lower().startswith("file:"):
        raise PrivacyConfigurationError(
            "Privacy Mode DATABASE_URL must name a database file"
        )
    db_path = confine_path(path_text, data_root, "DATABASE_URL database path")
    return f"{raw[:match.end()]}{db_path.as_posix()}"


def validate_loopback_http_url(
    raw: str,
    *,
    label: str = "URL",
) -> str:
    """Return a canonical local HTTP URL or raise.

    Model and paired-workspace traffic is deliberately narrower than the
    general IP definition of loopback: only numeric ``127.0.0.1`` with an
    explicit TCP port is an authority.
    """
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        parsed = None
        port = None
    if (
        parsed is None
        or parsed.scheme.lower() != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise PrivacyConfigurationError(
            f"{label} must use http://127.0.0.1 with an explicit port"
        )
    return value.rstrip("/")


_PRIVACY_DISABLED_STARTUP = frozenset(
    {
        "bg_monitor",
        "mcp_connections",
        "endpoint_warmups",
        "model_keepalive",
        "default_tasks",
        "task_scheduler",
        "nightly_skill_audit",
        "cookbook_lifecycle",
    }
)


def startup_capability_enabled(
    capability: str,
    *,
    profile: Optional[str] = None,
) -> bool:
    """Pure startup composition decision used by ``app._startup_event``."""
    return not (
        is_privacy_mode(profile) and str(capability) in _PRIVACY_DISABLED_STARTUP
    )


def load_profile_dotenv(
    loader: Callable[..., object],
    *,
    profile: Optional[str] = None,
    data_root: os.PathLike[str] | str | None = None,
    env_file: os.PathLike[str] | str | None = None,
    encoding: str = "utf-8-sig",
) -> bool:
    """Load dotenv without allowing Privacy Mode to read the repository file.

    Standard keeps the historical no-path ``load_dotenv`` call. Privacy loads
    nothing unless an explicit file is supplied, and that file must resolve
    below the already-selected private data root.
    """
    selected = PROFILE if profile is None else normalize_profile(profile)
    if selected == "standard":
        return bool(loader(encoding=encoding))

    selected_root = data_root or os.environ.get("ODYSSEUS_DATA_DIR")
    root = validate_privacy_data_root(selected_root)
    selected_env_file = (
        env_file if env_file is not None else os.environ.get("ODYSSEUS_ENV_FILE")
    )
    if selected_env_file is None or not str(selected_env_file).strip():
        return False
    confined = confine_path(selected_env_file, root, "ODYSSEUS_ENV_FILE")
    return bool(loader(dotenv_path=str(confined), encoding=encoding))


def parse_tor_socks_endpoint(raw: str) -> tuple[str, int]:
    """Parse the only SOCKS authority Privacy Mode is allowed to contact."""
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        parsed = None
        port = None
    if (
        parsed is None
        or parsed.scheme.lower() != "socks5h"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise PrivacyConfigurationError(
            "ODYSSEUS_TOR_SOCKS_URL must use socks5h://127.0.0.1 with an explicit port"
        )
    return "127.0.0.1", port


def tor_socks_ready(
    endpoint: tuple[str, int],
    *,
    timeout: float = 0.25,
    connector: Callable[..., object] = socket.create_connection,
) -> bool:
    """Return whether a TCP listener accepts connections at the Tor endpoint."""
    connection = None
    try:
        connection = connector(endpoint, timeout=timeout)
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


_PRIVACY_DISABLED_CAPABILITIES = (
    "background-automations",
    "cloud-models",
    "cookbook-downloads",
    "email-calendar-sync",
    "hosted-speech-embeddings",
    "network-mcp",
    "shell-automation",
    "webhooks",
)


def build_profile_status(
    *,
    profile: Optional[str] = None,
    environment: Optional[Mapping[str, str]] = None,
    tor_probe: Optional[Callable[[tuple[str, int]], bool]] = None,
) -> dict[str, object]:
    """Build the public, non-secret Standard/Privacy workspace status."""
    selected = PROFILE if profile is None else normalize_profile(profile)
    env = os.environ if environment is None else environment
    if selected == "privacy":
        counterpart = validate_loopback_http_url(
            env.get("ODYSSEUS_STANDARD_URL", "http://127.0.0.1:7000"),
            label="ODYSSEUS_STANDARD_URL",
        )
        ready = False
        try:
            endpoint = parse_tor_socks_endpoint(
                env.get("ODYSSEUS_TOR_SOCKS_URL", "")
            )
            ready = bool((tor_probe or tor_socks_ready)(endpoint))
        except PrivacyConfigurationError:
            ready = False
        transport = {"required": True, "ready": ready, "label": "Tor"}
        label = "Privacy Workspace"
        disabled = list(_PRIVACY_DISABLED_CAPABILITIES)
    else:
        counterpart = validate_loopback_http_url(
            env.get("ODYSSEUS_PRIVACY_URL", "http://127.0.0.1:7001"),
            label="ODYSSEUS_PRIVACY_URL",
        )
        transport = {"required": False, "ready": True, "label": "Direct"}
        label = "Standard Workspace"
        disabled = []

    return {
        "profile": selected,
        "label": label,
        "counterpart_url": counterpart + "/",
        "transport": transport,
        "data_isolated": True,
        "session_migration": False,
        "disabled_capabilities": disabled,
    }


_COOKIE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def session_cookie_name(
    *,
    profile: Optional[str] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Return a port-collision-safe cookie name for the selected process."""
    selected = PROFILE if profile is None else normalize_profile(profile)
    if selected == "standard":
        return "odysseus_session"
    env = os.environ if environment is None else environment
    value = (
        env["ODYSSEUS_SESSION_COOKIE"]
        if "ODYSSEUS_SESSION_COOKIE" in env
        else "odysseus_privacy_session"
    )
    if (
        not isinstance(value, str)
        or not _COOKIE_NAME.fullmatch(value)
        or value == "odysseus_session"
    ):
        raise PrivacyConfigurationError(
            "Privacy Mode requires a distinct safe ODYSSEUS_SESSION_COOKIE"
        )
    return value
