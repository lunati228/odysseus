from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.local_model_lifecycle import (
    LocalModelBusyError,
    LocalModelManagerError,
    LocalModelRegistryError,
    activate_local_model,
    get_local_model_inventory,
    manager_call,
)


def _write_registry(path: Path, *, host: str = "127.0.0.1") -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "odysseus-private-model-registry-1",
                "server": {"host": host, "port": 18085},
                "models": [
                    {
                        "key": "qwen",
                        "alias": "huihui-qwen3.8-27b-abliterated-q6-k-l",
                        "model": "C:/PRIVATE/MODEL/qwen.gguf",
                        "modelSha256": "SECRET_HASH",
                        "modelBytes": 24_945_124_640,
                        "mtpDefault": True,
                        "args": ["--ctx-size", "65536", "--reasoning-effort", "low"],
                    },
                    {
                        "key": "gemma12",
                        "alias": "gemma-4-12b-it-qat-q4-k-xl",
                        "model": "C:/PRIVATE/MODEL/gemma.gguf",
                        "modelSha256": "ANOTHER_SECRET_HASH",
                        "modelBytes": 6_716_356_800,
                        "mtpDefault": True,
                        "args": ["--ctx-size", "65536"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _manager_script(path: Path) -> Path:
    path.write_text("# fake manager", encoding="utf-8")
    return path


def test_inventory_is_sanitized_and_reports_ready_model(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)

    inventory = get_local_model_inventory(
        config_path=config,
        manager_script=script,
        manager_call_fn=lambda *_: {
            "modelRunning": True,
            "model": "qwen",
            "mtp": True,
        },
        probe_model_fn=lambda endpoint, alias: endpoint.endswith("/v1") and alias.startswith("huihui-"),
    )

    assert inventory == {
        "available": True,
        "state": "READY",
        "activeModelKey": "qwen",
        "endpointUrl": "http://127.0.0.1:18085/v1",
        "models": [
            {
                "key": "qwen",
                "alias": "huihui-qwen3.8-27b-abliterated-q6-k-l",
                "displayName": "Huihui Qwen3.8 27B Abliterated Q6 K L",
                "state": "READY",
                "isActive": True,
                "supportsReasoningEffort": True,
                "mtpDefault": True,
                "contextSize": 65536,
            },
            {
                "key": "gemma12",
                "alias": "gemma-4-12b-it-qat-q4-k-xl",
                "displayName": "Gemma 4 12B IT QAT Q4 K XL",
                "state": "AVAILABLE",
                "isActive": False,
                "supportsReasoningEffort": False,
                "mtpDefault": True,
                "contextSize": 65536,
            },
        ],
    }
    serialized = json.dumps(inventory)
    assert "C:/PRIVATE" not in serialized
    assert "SECRET_HASH" not in serialized
    assert "modelBytes" not in serialized


def test_inventory_reports_loading_until_alias_probe_succeeds(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)

    inventory = get_local_model_inventory(
        config_path=config,
        manager_script=script,
        manager_call_fn=lambda *_: {"modelRunning": True, "model": "gemma12"},
        probe_model_fn=lambda *_: False,
    )

    assert inventory["state"] == "LOADING"
    assert inventory["activeModelKey"] == "gemma12"
    assert next(m for m in inventory["models"] if m["key"] == "gemma12")["state"] == "LOADING"


def test_inventory_reports_stopped_with_loadable_models(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)

    inventory = get_local_model_inventory(
        config_path=config,
        manager_script=script,
        manager_call_fn=lambda *_: {"modelRunning": False, "model": "qwen"},
        probe_model_fn=lambda *_: pytest.fail("stopped model must not be probed"),
    )

    assert inventory["state"] == "STOPPED"
    assert inventory["activeModelKey"] is None
    assert {m["state"] for m in inventory["models"]} == {"AVAILABLE"}


def test_inventory_rejects_non_loopback_server(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config, host="example.com")

    with pytest.raises(LocalModelRegistryError, match="loopback"):
        get_local_model_inventory(config_path=config, manager_script=script)


def test_activate_rejects_unknown_model_before_manager_mutation(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)
    calls = []

    with pytest.raises(KeyError, match="unknown local model"):
        activate_local_model(
            "not-installed",
            config_path=config,
            manager_script=script,
            manager_call_fn=lambda *args: calls.append(args),
        )

    assert calls == []


def test_activate_ready_model_is_noop(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)
    actions = []

    def fake_manager(_script, action, model_key=None):
        actions.append((action, model_key))
        return {"modelRunning": True, "model": "qwen"}

    result = activate_local_model(
        "qwen",
        config_path=config,
        manager_script=script,
        manager_call_fn=fake_manager,
        probe_model_fn=lambda *_: True,
        warm_model_fn=lambda *_: pytest.fail("ready no-op must not warm again"),
    )

    assert result["changed"] is False
    assert result["state"] == "READY"
    assert actions == [("status", None)]


def test_activate_switches_models_in_stop_start_order_and_warms(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)
    actions = []
    current = {"running": True, "model": "qwen"}
    warmed = []

    def fake_manager(_script, action, model_key=None):
        actions.append((action, model_key))
        if action == "stop-model":
            current.update(running=False, model=None)
        elif action == "start-model":
            current.update(running=True, model=model_key)
        return {"modelRunning": current["running"], "model": current["model"]}

    result = activate_local_model(
        "gemma12",
        config_path=config,
        manager_script=script,
        manager_call_fn=fake_manager,
        probe_model_fn=lambda _endpoint, alias: current["running"] and alias.startswith("gemma-"),
        warm_model_fn=lambda endpoint, alias: warmed.append((endpoint, alias)) or True,
    )

    assert result["changed"] is True
    assert result["state"] == "READY"
    assert result["activeModelKey"] == "gemma12"
    assert result["warmup"] == "COMPLETED"
    assert actions == [
        ("status", None),
        ("stop-model", None),
        ("start-model", "gemma12"),
        ("status", None),
    ]
    assert warmed == [("http://127.0.0.1:18085/v1", "gemma-4-12b-it-qat-q4-k-xl")]


def test_activate_force_restarts_same_model(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)
    actions = []

    def fake_manager(_script, action, model_key=None):
        actions.append((action, model_key))
        return {"modelRunning": action != "stop-model", "model": model_key or "qwen"}

    result = activate_local_model(
        "qwen",
        force_restart=True,
        config_path=config,
        manager_script=script,
        manager_call_fn=fake_manager,
        probe_model_fn=lambda *_: True,
        warm_model_fn=lambda *_: True,
    )

    assert result["changed"] is True
    assert actions[:3] == [("status", None), ("stop-model", None), ("start-model", "qwen")]


def test_activate_rejects_concurrent_switch(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)
    lock = threading.Lock()
    lock.acquire()
    try:
        with pytest.raises(LocalModelBusyError):
            activate_local_model(
                "qwen",
                config_path=config,
                manager_script=script,
                change_lock=lock,
            )
    finally:
        lock.release()


def test_manager_call_uses_argv_without_shell_and_redacts_stderr(tmp_path, monkeypatch):
    script = _manager_script(tmp_path / "manager.ps1")
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=17, stdout="", stderr="PRIVATE_RUNTIME_PATH_AND_SECRET")

    monkeypatch.setattr("src.local_model_lifecycle.subprocess.run", fake_run)

    with pytest.raises(LocalModelManagerError) as exc_info:
        manager_call(script, "start-model", "qwen")

    assert observed["kwargs"]["shell"] is False
    assert "capture_output" not in observed["kwargs"]
    assert observed["kwargs"]["stdout"] is subprocess.DEVNULL
    assert observed["kwargs"]["stderr"] is subprocess.DEVNULL
    assert observed["argv"][-4:] == ["-Action", "start-model", "-Model", "qwen"]
    assert "PRIVATE_RUNTIME_PATH_AND_SECRET" not in str(exc_info.value)
    assert "17" in str(exc_info.value)


def test_manager_status_captures_only_the_short_lived_json_response(tmp_path, monkeypatch):
    script = _manager_script(tmp_path / "manager.ps1")
    observed = {}

    def fake_run(argv, **kwargs):
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"modelRunning": False, "model": None}),
            stderr="",
        )

    monkeypatch.setattr("src.local_model_lifecycle.subprocess.run", fake_run)

    result = manager_call(script, "status")

    assert observed["kwargs"]["capture_output"] is True
    assert "stdout" not in observed["kwargs"]
    assert "stderr" not in observed["kwargs"]
    assert result["modelRunning"] is False


def test_manager_call_rejects_unknown_action_without_spawning(tmp_path, monkeypatch):
    script = _manager_script(tmp_path / "manager.ps1")
    spawned = []
    monkeypatch.setattr(
        "src.local_model_lifecycle.subprocess.run",
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="unsupported manager action"):
        manager_call(script, "destroy-everything", "qwen")

    assert spawned == []


def test_activate_fails_closed_when_manager_does_not_report_target_ready(tmp_path):
    config = tmp_path / "models.json"
    script = _manager_script(tmp_path / "manager.ps1")
    _write_registry(config)
    status_calls = 0

    def fake_manager(_script, action, model_key=None):
        nonlocal status_calls
        if action == "status":
            status_calls += 1
            if status_calls == 1:
                return {"modelRunning": False, "model": None}
            return {"modelRunning": True, "model": model_key or "qwen"}
        return {}

    with pytest.raises(LocalModelManagerError, match="not ready"):
        activate_local_model(
            "qwen",
            config_path=config,
            manager_script=script,
            manager_call_fn=fake_manager,
            probe_model_fn=lambda *_: False,
            warm_model_fn=lambda *_: True,
        )
