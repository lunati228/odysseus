"""Local Qwen reasoning-effort control.

This module owns the small contract between the Odysseus UI and the external
Odysseus-Private installation:

* config/models.json is the registry the PowerShell manager reads when it
  launches llama-server. The only key this module ever mutates is the value
  following --reasoning-effort in the qwen entry's args list.
* The PowerShell manager script owns the model process. This module never
  starts or stops a model server itself; it only asks the manager to do so
  (stop-model followed by start-model -Model qwen) after a config rewrite,
  and only when the manager reports that Qwen is running.

Paths are injectable (config_path / manager_script) so tests exercise the
same code against temporary files without touching the real installation.
Production paths come from environment variables or from the manager-owned
data root; no operator-specific installation path is committed here.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

MODEL_KEY = "qwen"
REASONING_EFFORT_FLAG = "--reasoning-effort"
# Qwen3.8-27B chat template (embedded in the GGUF) accepts exactly
# xhigh/medium/low and raises for any other value. xhigh is the template's
# own default when no effort is supplied.
REASONING_LEVELS = ("low", "medium", "xhigh")
DEFAULT_REASONING_LEVEL = "xhigh"

_STATUS_TIMEOUT_SECONDS = 15
_STOP_TIMEOUT_SECONDS = 30
_START_TIMEOUT_SECONDS = 330

_PRIVATE_HOME_ENV = "ODYSSEUS_PRIVATE_HOME"
_MODEL_CONFIG_ENV = "ODYSSEUS_LOCAL_MODEL_CONFIG"
_MANAGER_SCRIPT_ENV = "ODYSSEUS_LOCAL_MANAGER_SCRIPT"


def _default_private_home() -> Path:
    configured = os.environ.get(_PRIVATE_HOME_ENV)
    if configured:
        return Path(configured)

    data_dir = os.environ.get("ODYSSEUS_DATA_DIR")
    if data_dir:
        data_path = Path(data_dir)
        if os.environ.get("ODYSSEUS_PROFILE", "").lower() == "privacy":
            # The manager places private data below <home>/privacy-vault/data.
            return data_path.parent.parent
        return data_path.parent

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Odysseus-Private"
    return Path.home() / ".odysseus-private"


def default_config_path() -> Path:
    """Return the models.json path, overridable via environment variable."""
    configured = os.environ.get(_MODEL_CONFIG_ENV)
    if configured:
        return Path(configured)
    return _default_private_home() / "config" / "models.json"


def default_manager_script() -> Path:
    """Return the manager script path, overridable via environment variable."""
    configured = os.environ.get(_MANAGER_SCRIPT_ENV)
    if configured:
        return Path(configured)
    return _default_private_home() / "bin" / "Odysseus-Private.ps1"


def _load_registry(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def _find_qwen(data: dict) -> Optional[dict]:
    for model in data.get("models") or []:
        if model.get("key") == MODEL_KEY:
            return model
    return None


def _set_effort(args: list, level: str) -> None:
    """Replace the value after --reasoning-effort, or append both."""
    for i, arg in enumerate(args):
        if arg == REASONING_EFFORT_FLAG:
            if i + 1 < len(args):
                args[i + 1] = level
            else:
                args.append(level)
            return
    args.extend([REASONING_EFFORT_FLAG, level])


def _atomic_write_registry(path: Path, data: dict) -> None:
    # Imported lazily so this module stays importable without the full
    # core package (core/__init__.py pulls in the LLM stack).
    from core.atomic_io import atomic_write_json

    atomic_write_json(str(path), data, indent=2)


def get_reasoning_level(config_path: Optional[Path] = None) -> str:
    """Return the configured Qwen reasoning-effort level (default xhigh)."""
    path = Path(config_path) if config_path is not None else default_config_path()
    data = _load_registry(path)
    model = _find_qwen(data)
    if model is None:
        return DEFAULT_REASONING_LEVEL
    args = model.get("args")
    if not isinstance(args, list):
        return DEFAULT_REASONING_LEVEL
    for i, arg in enumerate(args):
        if arg == REASONING_EFFORT_FLAG and i + 1 < len(args):
            value = args[i + 1]
            if isinstance(value, str) and value.lower() in REASONING_LEVELS:
                return value.lower()
            return DEFAULT_REASONING_LEVEL
    return DEFAULT_REASONING_LEVEL


def set_reasoning_level(
    level: str,
    *,
    config_path: Optional[Path] = None,
    manager_script: Optional[Path] = None,
    is_running: Optional[Callable[[], bool]] = None,
    restart: Optional[Callable[[], None]] = None,
) -> dict:
    """Persist level for qwen and restart the model when it is running.

    is_running and restart are injectable for tests. By default the
    manager script's status action decides whether Qwen is running and the
    manager script itself performs the restart.
    """
    if level not in REASONING_LEVELS:
        raise ValueError(
            f"reasoning level must be one of {', '.join(REASONING_LEVELS)}"
        )

    path = Path(config_path) if config_path is not None else default_config_path()
    data = _load_registry(path)
    model = _find_qwen(data)
    if model is None:
        raise KeyError(f"no {MODEL_KEY!r} entry in model registry")

    args = model.get("args")
    if not isinstance(args, list):
        args = []
        model["args"] = args
    _set_effort(args, level)
    _atomic_write_registry(path, data)

    script = Path(manager_script) if manager_script is not None else default_manager_script()
    if is_running is not None:
        running = bool(is_running())
    else:
        running = manager_reports_qwen_running(script)

    restarted = False
    restart_error = None
    if running:
        if restart is None:
            restart = lambda: restart_via_manager(script)  # noqa: E731
        try:
            restart()
            restarted = True
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            restart_error = f"{type(exc).__name__}: {exc}"

    return {
        "ok": True,
        "level": level,
        "restart_scheduled": restarted,
        "restart_error": restart_error,
    }


def _manager_argv(manager_script: Path, action: str, model: Optional[str] = None) -> list:
    argv = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(manager_script),
        "-Action",
        action,
    ]
    if model is not None:
        argv += ["-Model", model]
    return argv


def manager_reports_qwen_running(manager_script: Path) -> bool:
    """Ask the manager's status action whether the qwen model is live."""
    script = Path(manager_script)
    if not script.is_file():
        return False
    try:
        completed = subprocess.run(
            _manager_argv(script, "status"),
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        return False
    return payload.get("modelRunning") is True and payload.get("model") == MODEL_KEY


def restart_via_manager(manager_script: Path) -> None:
    """Stop then start the managed qwen model through the PowerShell manager."""
    script = Path(manager_script)
    if not script.is_file():
        raise FileNotFoundError(f"manager script not found: {script}")

    stop = subprocess.run(
        _manager_argv(script, "stop-model"),
        capture_output=True,
        text=True,
        timeout=_STOP_TIMEOUT_SECONDS,
    )
    if stop.returncode != 0:
        raise RuntimeError(f"manager stop-model failed with exit code {stop.returncode}")

    start = subprocess.run(
        _manager_argv(script, "start-model", MODEL_KEY),
        capture_output=True,
        text=True,
        timeout=_START_TIMEOUT_SECONDS,
    )
    if start.returncode != 0:
        raise RuntimeError(f"manager start-model failed with exit code {start.returncode}")
