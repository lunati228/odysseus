"""Sanitized lifecycle control for the installed local llama.cpp models.

The external PowerShell manager remains the process owner.  This module gives
the web UI a narrow interface for listing the manager's installed models and
switching between them without exposing model paths, hashes, log paths, or
manager stdout/stderr.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from src.qwen_reasoning import default_config_path, default_manager_script


_MANAGER_ACTIONS = frozenset({"status", "start-model", "stop-model"})
_MODEL_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_MANAGER_TIMEOUTS = {
    "status": 15,
    "stop-model": 30,
    "start-model": 360,
}
_MAX_HTTP_RESPONSE_BYTES = 1024 * 1024
_MODEL_CHANGE_LOCK = threading.Lock()


class LocalModelError(RuntimeError):
    """Base class for sanitized local-model lifecycle failures."""


class LocalModelRegistryError(LocalModelError):
    """The installed model registry is missing, malformed, or unsafe."""


class LocalModelManagerError(LocalModelError):
    """The external process manager failed or returned invalid state."""


class LocalModelBusyError(LocalModelError):
    """Another model stop/start/restart operation is already in progress."""


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _load_registry(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise LocalModelRegistryError("local model registry is unavailable") from exc
    if not isinstance(data, dict):
        raise LocalModelRegistryError("local model registry root must be an object")

    server = data.get("server")
    models = data.get("models")
    if not isinstance(server, dict) or not isinstance(models, list):
        raise LocalModelRegistryError("local model registry has an invalid schema")

    host = server.get("host")
    port = server.get("port")
    if not isinstance(host, str) or not _is_loopback_host(host):
        raise LocalModelRegistryError("local model server must use a loopback host")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise LocalModelRegistryError("local model server port is invalid")

    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise LocalModelRegistryError("local model entry must be an object")
        key = model.get("key")
        alias = model.get("alias")
        if not isinstance(key, str) or not _MODEL_KEY_RE.fullmatch(key):
            raise LocalModelRegistryError("local model key is invalid")
        if key in seen:
            raise LocalModelRegistryError("local model keys must be unique")
        seen.add(key)
        if not isinstance(alias, str) or not alias.strip() or len(alias) > 256:
            raise LocalModelRegistryError("local model alias is invalid")

    return data


def _endpoint_url(registry: dict) -> str:
    server = registry["server"]
    host = str(server["host"]).strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(server['port'])}/v1"


def _context_size(model: dict) -> Optional[int]:
    args = model.get("args")
    if not isinstance(args, list):
        return None
    try:
        index = args.index("--ctx-size")
        value = int(args[index + 1])
    except (ValueError, TypeError, IndexError):
        return None
    return value if value > 0 else None


def _display_name(model: dict) -> str:
    configured = model.get("displayName")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()[:160]

    def pretty(token: str) -> str:
        lower = token.lower()
        if lower in {"it", "qat", "xl", "bf16"}:
            return lower.upper()
        if re.fullmatch(r"\d+b", lower):
            return lower[:-1] + "B"
        if re.fullmatch(r"q\d+", lower):
            return lower.upper()
        if len(lower) == 1:
            return lower.upper()
        return lower.capitalize()

    parts = [part for part in re.split(r"[-_]+", str(model["alias"])) if part]
    return " ".join(pretty(part) for part in parts)


def _manager_argv(manager_script: Path, action: str, model_key: Optional[str]) -> list[str]:
    if action not in _MANAGER_ACTIONS:
        raise ValueError(f"unsupported manager action: {action}")
    if model_key is not None and not _MODEL_KEY_RE.fullmatch(model_key):
        raise ValueError("invalid local model key")
    if action == "start-model" and model_key is None:
        raise ValueError("start-model requires a local model key")
    if action != "start-model" and model_key is not None:
        raise ValueError(f"{action} does not accept a local model key")

    powershell = "powershell.exe"
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidate = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if candidate.is_file():
            powershell = str(candidate)

    argv = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(manager_script),
        "-Action",
        action,
    ]
    if model_key is not None:
        argv.extend(["-Model", model_key])
    return argv


def manager_call(
    manager_script: Path,
    action: str,
    model_key: Optional[str] = None,
) -> dict:
    """Run one allowlisted manager action and return sanitized status data."""
    script = Path(manager_script)
    if not script.is_file():
        raise LocalModelManagerError("local model manager is unavailable")
    argv = _manager_argv(script, action, model_key)
    kwargs = {
        "text": True,
        "timeout": _MANAGER_TIMEOUTS[action],
        "shell": False,
    }
    if action == "status":
        # Status is a short-lived sanitized JSON response.  Long-lived model
        # children can inherit Windows pipe handles from Start-Process, so
        # capturing start/stop output can keep communicate() waiting even
        # after the manager PowerShell process has exited.
        kwargs["capture_output"] = True
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(argv, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalModelManagerError(f"local model manager {action} did not complete") from exc
    if completed.returncode != 0:
        raise LocalModelManagerError(
            f"local model manager {action} failed with exit code {completed.returncode}"
        )
    if action != "status":
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise LocalModelManagerError("local model manager returned invalid status") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("modelRunning"), bool):
        raise LocalModelManagerError("local model manager returned invalid status")
    model = payload.get("model")
    if model is not None and not isinstance(model, str):
        raise LocalModelManagerError("local model manager returned invalid status")
    return {
        "modelRunning": payload["modelRunning"],
        "model": model,
        "mtp": payload.get("mtp") if isinstance(payload.get("mtp"), bool) else None,
    }


def _validate_loopback_endpoint(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url)
    if parsed.scheme != "http" or not parsed.hostname or not _is_loopback_host(parsed.hostname):
        raise LocalModelRegistryError("local model endpoint must be loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LocalModelRegistryError("local model endpoint is invalid")
    return endpoint_url.rstrip("/")


def _direct_http_open(request: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def probe_model(endpoint_url: str, expected_alias: str) -> bool:
    """Return true only when the loopback OpenAI endpoint serves the alias."""
    try:
        base = _validate_loopback_endpoint(endpoint_url)
        request = urllib.request.Request(
            f"{base}/models",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with _direct_http_open(request, 2.0) as response:
            raw = response.read(_MAX_HTTP_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_HTTP_RESPONSE_BYTES:
            return False
        payload = json.loads(raw.decode("utf-8"))
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return False
        return any(
            isinstance(item, dict) and item.get("id") == expected_alias
            for item in models
        )
    except (OSError, ValueError, UnicodeError, urllib.error.URLError):
        return False


def warm_model(endpoint_url: str, expected_alias: str) -> bool:
    """Prime the first chat graph with a fixed synthetic one-token request."""
    try:
        base = _validate_loopback_endpoint(endpoint_url)
        body = json.dumps(
            {
                "model": expected_alias,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Generate a deterministic sequence of short numbered facts "
                            "about arithmetic. Continue until the generation limit; do "
                            "not stop early or summarize."
                        ),
                    }
                ],
                "max_tokens": 1,
                "temperature": 0,
                "stream": False,
                "cache_prompt": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        with _direct_http_open(request, 120.0) as response:
            raw = response.read(_MAX_HTTP_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_HTTP_RESPONSE_BYTES:
            return False
        payload = json.loads(raw.decode("utf-8"))
        return isinstance(payload, dict) and isinstance(payload.get("choices"), list)
    except (OSError, ValueError, UnicodeError, urllib.error.URLError):
        return False


def get_local_model_inventory(
    *,
    config_path: Optional[Path] = None,
    manager_script: Optional[Path] = None,
    manager_call_fn: Optional[Callable[[Path, str, Optional[str]], dict]] = None,
    probe_model_fn: Optional[Callable[[str, str], bool]] = None,
) -> dict:
    """Return installed models and current process state without sensitive data."""
    registry = _load_registry(Path(config_path) if config_path is not None else default_config_path())
    script = Path(manager_script) if manager_script is not None else default_manager_script()
    call_manager = manager_call_fn or manager_call
    probe = probe_model_fn or probe_model
    status = call_manager(script, "status", None)

    models_by_key = {model["key"]: model for model in registry["models"]}
    running = status.get("modelRunning") is True
    reported_key = status.get("model") if running else None
    active_key = reported_key if reported_key in models_by_key else None
    endpoint = _endpoint_url(registry)

    if running and active_key is None:
        overall_state = "ERROR"
    elif active_key is None:
        overall_state = "STOPPED"
    else:
        active_alias = models_by_key[active_key]["alias"]
        overall_state = "READY" if probe(endpoint, active_alias) else "LOADING"

    public_models = []
    for model in registry["models"]:
        is_active = model["key"] == active_key
        public_models.append(
            {
                "key": model["key"],
                "alias": model["alias"],
                "displayName": _display_name(model),
                "state": overall_state if is_active else "AVAILABLE",
                "isActive": is_active,
                "supportsReasoningEffort": model["key"] == "qwen",
                "mtpDefault": bool(model.get("mtpDefault")),
                "contextSize": _context_size(model),
            }
        )

    return {
        "available": True,
        "state": overall_state,
        "activeModelKey": active_key,
        "endpointUrl": endpoint,
        "models": public_models,
    }


def activate_local_model(
    model_key: str,
    *,
    force_restart: bool = False,
    config_path: Optional[Path] = None,
    manager_script: Optional[Path] = None,
    manager_call_fn: Optional[Callable[[Path, str, Optional[str]], dict]] = None,
    probe_model_fn: Optional[Callable[[str, str], bool]] = None,
    warm_model_fn: Optional[Callable[[str, str], bool]] = None,
    change_lock=None,
) -> dict:
    """Switch to one installed model and return only after it is ready."""
    config = Path(config_path) if config_path is not None else default_config_path()
    registry = _load_registry(config)
    models_by_key = {model["key"]: model for model in registry["models"]}
    if model_key not in models_by_key:
        raise KeyError(f"unknown local model: {model_key}")

    script = Path(manager_script) if manager_script is not None else default_manager_script()
    call_manager = manager_call_fn or manager_call
    probe = probe_model_fn or probe_model
    warmer = warm_model_fn or warm_model
    lock = change_lock or _MODEL_CHANGE_LOCK
    if not lock.acquire(False):
        raise LocalModelBusyError("another local model change is already in progress")

    try:
        current = get_local_model_inventory(
            config_path=config,
            manager_script=script,
            manager_call_fn=call_manager,
            probe_model_fn=probe,
        )
        if (
            not force_restart
            and current["activeModelKey"] == model_key
            and current["state"] == "READY"
        ):
            return {**current, "changed": False, "warmup": "SKIPPED"}

        if current["activeModelKey"] is not None or current["state"] == "ERROR":
            call_manager(script, "stop-model", None)
        call_manager(script, "start-model", model_key)

        ready = get_local_model_inventory(
            config_path=config,
            manager_script=script,
            manager_call_fn=call_manager,
            probe_model_fn=probe,
        )
        if ready["activeModelKey"] != model_key or ready["state"] != "READY":
            raise LocalModelManagerError("selected local model is not ready")

        target = models_by_key[model_key]
        try:
            warmed = bool(warmer(ready["endpointUrl"], target["alias"]))
        except Exception:  # noqa: BLE001 - warm-up is best effort and sanitized
            warmed = False
        return {
            **ready,
            "changed": True,
            "warmup": "COMPLETED" if warmed else "FAILED",
        }
    finally:
        lock.release()


__all__ = [
    "LocalModelBusyError",
    "LocalModelError",
    "LocalModelManagerError",
    "LocalModelRegistryError",
    "activate_local_model",
    "get_local_model_inventory",
    "manager_call",
    "probe_model",
    "warm_model",
]
