"""Focused tests for src.qwen_reasoning (local Qwen reasoning effort).

The tests use temporary registries and injected restart/running callables, so
they never touch the real Odysseus-Private installation or its live model.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.qwen_reasoning import (
    DEFAULT_REASONING_LEVEL,
    MODEL_KEY,
    default_config_path,
    default_manager_script,
    get_reasoning_level,
    manager_reports_qwen_running,
    restart_via_manager,
    set_reasoning_level,
)


def _write_registry(path: Path, level: str = "medium", args=None) -> None:
    if args is None:
        args = [
            "--ctx-size", "262144",
            "--reasoning", "auto",
            "--reasoning-effort", level,
            "--temp", "1.0",
        ]
    payload = {
        "schema": "odysseus-private-model-registry-1",
        "server": {"host": "127.0.0.1", "port": 18085},
        "models": [
            {
                "key": "qwen",
                "alias": "huihui-qwen3.8-27b-abliterated-q6-k-l",
                "args": args,
            },
            {
                "key": "gemma12",
                "alias": "gemma-4-12b-it-qat-q4-k-xl",
                "args": ["--ctx-size", "65536"],
            },
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8-sig")


def _manager_script(tmp_path: Path) -> Path:
    script = tmp_path / "Odysseus-Private.ps1"
    script.write_text("# fake manager", encoding="utf-8")
    return script


def test_default_paths_follow_private_manager_home(tmp_path, monkeypatch):
    private_home = tmp_path / "private-runtime"
    monkeypatch.setenv("ODYSSEUS_PRIVATE_HOME", str(private_home))
    monkeypatch.delenv("ODYSSEUS_LOCAL_MODEL_CONFIG", raising=False)
    monkeypatch.delenv("ODYSSEUS_LOCAL_MANAGER_SCRIPT", raising=False)

    assert default_config_path() == private_home / "config" / "models.json"
    assert default_manager_script() == (
        private_home / "bin" / "Odysseus-Private.ps1"
    )


def test_default_paths_derive_manager_home_from_privacy_data_root(
    tmp_path, monkeypatch
):
    private_home = tmp_path / "private-runtime"
    data_root = private_home / "privacy-vault" / "data"
    monkeypatch.delenv("ODYSSEUS_PRIVATE_HOME", raising=False)
    monkeypatch.delenv("ODYSSEUS_LOCAL_MODEL_CONFIG", raising=False)
    monkeypatch.delenv("ODYSSEUS_LOCAL_MANAGER_SCRIPT", raising=False)
    monkeypatch.setenv("ODYSSEUS_PROFILE", "privacy")
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(data_root))

    assert default_config_path() == private_home / "config" / "models.json"
    assert default_manager_script() == (
        private_home / "bin" / "Odysseus-Private.ps1"
    )


# ── get_reasoning_level ──


def test_get_reasoning_level_reads_configured_value(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config, level="xhigh")

    assert get_reasoning_level(config) == "xhigh"


def test_get_reasoning_level_defaults_when_flag_missing(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config, args=["--ctx-size", "262144"])

    assert get_reasoning_level(config) == DEFAULT_REASONING_LEVEL


def test_get_reasoning_level_defaults_when_value_invalid(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config, args=["--reasoning-effort", "ultra"])

    assert get_reasoning_level(config) == DEFAULT_REASONING_LEVEL


def test_get_reasoning_level_defaults_when_qwen_entry_missing(tmp_path):
    config = tmp_path / "models.json"
    config.write_text(
        json.dumps({"models": [{"key": "gemma12", "args": []}]}),
        encoding="utf-8-sig",
    )

    assert get_reasoning_level(config) == DEFAULT_REASONING_LEVEL


def test_get_reasoning_level_defaults_when_args_not_a_list(tmp_path):
    config = tmp_path / "models.json"
    config.write_text(
        json.dumps({"models": [{"key": "qwen", "args": "not-a-list"}]}),
        encoding="utf-8-sig",
    )

    assert get_reasoning_level(config) == DEFAULT_REASONING_LEVEL


# ── validation ──


# The Qwen3.8-27B template accepts xhigh/medium/low only; "high", "max",
# "minimal", and "default" all raise a Jinja exception at inference time.
@pytest.mark.parametrize("level", ["max", "ultra", "", "HIGH", " medium ", "high"])
def test_set_reasoning_level_rejects_invalid_levels(tmp_path, level):
    config = tmp_path / "models.json"
    _write_registry(config)

    with pytest.raises(ValueError):
        set_reasoning_level(level, config_path=config)


# ── config rewrite ──


def test_set_reasoning_level_rewrites_only_effort_value(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config, level="medium")

    set_reasoning_level("low", config_path=config, is_running=lambda: False)

    payload = json.loads(config.read_text(encoding="utf-8-sig"))
    qwen = next(m for m in payload["models"] if m["key"] == MODEL_KEY)
    assert qwen["args"][qwen["args"].index("--reasoning-effort") + 1] == "low"
    # Every unrelated key and argument is preserved byte-for-byte.
    assert payload["schema"] == "odysseus-private-model-registry-1"
    assert payload["server"] == {"host": "127.0.0.1", "port": 18085}
    assert qwen["alias"] == "huihui-qwen3.8-27b-abliterated-q6-k-l"
    assert "--ctx-size" in qwen["args"]
    assert "--reasoning" in qwen["args"]
    assert "--temp" in qwen["args"]
    assert qwen["args"][qwen["args"].index("--temp") + 1] == "1.0"
    gemma = next(m for m in payload["models"] if m["key"] == "gemma12")
    assert gemma["args"] == ["--ctx-size", "65536"]


def test_set_reasoning_level_appends_flag_when_missing(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config, args=["--ctx-size", "262144"])

    set_reasoning_level("xhigh", config_path=config, is_running=lambda: False)

    payload = json.loads(config.read_text(encoding="utf-8-sig"))
    qwen = next(m for m in payload["models"] if m["key"] == MODEL_KEY)
    assert qwen["args"][-2:] == ["--reasoning-effort", "xhigh"]


def test_set_reasoning_level_persists_xhigh(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config, level="medium")

    set_reasoning_level("xhigh", config_path=config, is_running=lambda: False)

    assert get_reasoning_level(config) == "xhigh"


def test_set_reasoning_level_leaves_no_tmp_file(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config)

    set_reasoning_level("low", config_path=config, is_running=lambda: False)

    assert list(tmp_path.glob("models.json.tmp.*")) == []


def test_set_reasoning_level_raises_when_qwen_missing(tmp_path):
    config = tmp_path / "models.json"
    config.write_text(
        json.dumps({"models": [{"key": "gemma12", "args": []}]}),
        encoding="utf-8-sig",
    )
    before = config.read_text(encoding="utf-8-sig")

    with pytest.raises(KeyError):
        set_reasoning_level("low", config_path=config, is_running=lambda: False)

    assert config.read_text(encoding="utf-8-sig") == before


# ── restart gating ──


def test_set_reasoning_level_persists_without_restart_when_not_running(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config)
    restart = MagicMock()

    result = set_reasoning_level(
        "xhigh", config_path=config, is_running=lambda: False, restart=restart
    )

    assert result["ok"] is True
    assert result["level"] == "xhigh"
    assert result["restart_scheduled"] is False
    assert result["restart_error"] is None
    restart.assert_not_called()
    payload = json.loads(config.read_text(encoding="utf-8-sig"))
    qwen = next(m for m in payload["models"] if m["key"] == MODEL_KEY)
    assert qwen["args"][qwen["args"].index("--reasoning-effort") + 1] == "xhigh"


def test_set_reasoning_level_restarts_when_running(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config)
    restart = MagicMock()

    result = set_reasoning_level(
        "low", config_path=config, is_running=lambda: True, restart=restart
    )

    assert result["restart_scheduled"] is True
    restart.assert_called_once_with()


def test_set_reasoning_level_surfaces_restart_failure(tmp_path):
    config = tmp_path / "models.json"
    _write_registry(config)

    def failing_restart():
        raise RuntimeError("start-model failed")

    result = set_reasoning_level(
        "low", config_path=config, is_running=lambda: True, restart=failing_restart
    )

    assert result["restart_scheduled"] is False
    assert result["restart_error"] == "RuntimeError: start-model failed"


# ── manager status parsing ──


def test_manager_reports_qwen_running_true(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"modelRunning": True, "model": "qwen"}),
        )

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    assert manager_reports_qwen_running(script) is True


def test_manager_reports_qwen_running_false_for_gemma(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"modelRunning": True, "model": "gemma12"}),
        )

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    assert manager_reports_qwen_running(script) is False


def test_manager_reports_qwen_running_false_when_not_running(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"modelRunning": False, "model": "qwen"}),
        )

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    assert manager_reports_qwen_running(script) is False


def test_manager_reports_qwen_running_false_when_script_missing(tmp_path):
    assert manager_reports_qwen_running(tmp_path / "missing.ps1") is False


def test_manager_reports_qwen_running_false_on_nonzero_status(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    assert manager_reports_qwen_running(script) is False


# ── restart via manager ──


def test_restart_via_manager_runs_stop_then_start(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    restart_via_manager(script)

    assert len(calls) == 2
    assert calls[0][calls[0].index("-Action") + 1] == "stop-model"
    assert calls[1][calls[1].index("-Action") + 1] == "start-model"
    assert calls[1][calls[1].index("-Model") + 1] == MODEL_KEY


def test_restart_via_manager_raises_when_stop_fails(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="stop-model"):
        restart_via_manager(script)


def test_restart_via_manager_raises_when_start_fails(tmp_path, monkeypatch):
    script = _manager_script(tmp_path)

    def fake_run(argv, **kwargs):
        action = argv[argv.index("-Action") + 1]
        return SimpleNamespace(
            returncode=0 if action == "stop-model" else 1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("src.qwen_reasoning.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="start-model"):
        restart_via_manager(script)


def test_restart_via_manager_raises_when_script_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        restart_via_manager(tmp_path / "missing.ps1")
