from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import routes.model_routes as model_routes
from src.local_model_lifecycle import (
    LocalModelBusyError,
    LocalModelManagerError,
    LocalModelRegistryError,
)


def _router():
    return model_routes.setup_model_routes(lambda *_args, **_kwargs: [])


def _endpoint(router, path: str, method: str = "GET"):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"missing route: {method} {path}")


def _inventory(state="READY", active="qwen"):
    return {
        "available": True,
        "state": state,
        "activeModelKey": active,
        "endpointUrl": "http://127.0.0.1:18085/v1",
        "models": [
            {
                "key": "qwen",
                "alias": "qwen-alias",
                "displayName": "Qwen",
                "state": state if active == "qwen" else "AVAILABLE",
                "isActive": active == "qwen",
                "supportsReasoningEffort": True,
                "mtpDefault": True,
                "contextSize": 65536,
            }
        ],
    }


def test_get_local_models_returns_sanitized_lifecycle(monkeypatch):
    expected = _inventory()
    monkeypatch.setattr(
        model_routes._local_models,
        "get_local_model_inventory",
        lambda: expected,
    )

    result = _endpoint(_router(), "/api/local-models")()

    assert result == expected


@pytest.mark.parametrize("error", [OSError("private path"), LocalModelRegistryError("private path")])
def test_get_local_models_degrades_to_unavailable_without_leaking(monkeypatch, error):
    def fail():
        raise error

    monkeypatch.setattr(model_routes._local_models, "get_local_model_inventory", fail)

    result = _endpoint(_router(), "/api/local-models")()

    assert result == {
        "available": False,
        "state": "UNAVAILABLE",
        "activeModelKey": None,
        "endpointUrl": None,
        "models": [],
    }
    assert "private path" not in str(result)


def test_activate_local_model_requires_admin_and_returns_ready(monkeypatch):
    require_admin = MagicMock()
    monkeypatch.setattr(model_routes, "require_admin", require_admin)
    expected = {**_inventory(active="gemma12"), "changed": True, "warmup": "COMPLETED"}
    activate = MagicMock(return_value=expected)
    monkeypatch.setattr(model_routes._local_models, "activate_local_model", activate)
    request = SimpleNamespace()

    result = _endpoint(
        _router(), "/api/local-models/{model_key}/activation", "POST"
    )("gemma12", request)

    require_admin.assert_called_once_with(request)
    activate.assert_called_once_with("gemma12")
    assert result == expected


def test_activate_local_model_maps_unknown_to_404(monkeypatch):
    monkeypatch.setattr(model_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        model_routes._local_models,
        "activate_local_model",
        lambda _key: (_ for _ in ()).throw(KeyError("private model path")),
    )

    with pytest.raises(HTTPException) as exc_info:
        _endpoint(_router(), "/api/local-models/{model_key}/activation", "POST")(
            "unknown", SimpleNamespace()
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "local model not found"


def test_activate_local_model_maps_busy_to_409(monkeypatch):
    monkeypatch.setattr(model_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        model_routes._local_models,
        "activate_local_model",
        lambda _key: (_ for _ in ()).throw(LocalModelBusyError("private")),
    )

    with pytest.raises(HTTPException) as exc_info:
        _endpoint(_router(), "/api/local-models/{model_key}/activation", "POST")(
            "qwen", SimpleNamespace()
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "another local model change is already in progress"


def test_activate_local_model_redacts_manager_failure(monkeypatch):
    monkeypatch.setattr(model_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        model_routes._local_models,
        "activate_local_model",
        lambda _key: (_ for _ in ()).throw(
            LocalModelManagerError("C:/private/log SECRET")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        _endpoint(_router(), "/api/local-models/{model_key}/activation", "POST")(
            "qwen", SimpleNamespace()
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "local model change failed"


def test_reasoning_change_waits_for_active_qwen_restart(monkeypatch):
    monkeypatch.setattr(model_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        model_routes._local_models,
        "get_local_model_inventory",
        lambda: _inventory(state="READY", active="qwen"),
    )
    persist = MagicMock(
        return_value={
            "ok": True,
            "level": "medium",
            "restart_scheduled": False,
            "restart_error": None,
        }
    )
    monkeypatch.setattr(model_routes._qwen_reasoning, "set_reasoning_level", persist)
    restart = MagicMock(
        return_value={**_inventory(state="READY", active="qwen"), "warmup": "COMPLETED"}
    )
    monkeypatch.setattr(model_routes._local_models, "activate_local_model", restart)
    result = _endpoint(_router(), "/api/models/qwen/reasoning-effort", "POST")(
        SimpleNamespace(level="medium"), SimpleNamespace()
    )

    persist.assert_called_once()
    persist_args, persist_kwargs = persist.call_args
    assert persist_args == ("medium",)
    assert persist_kwargs["is_running"]() is False
    restart.assert_called_once_with("qwen", force_restart=True)
    assert result["restart_scheduled"] is True
    assert result["restart_completed"] is True
    assert result["state"] == "READY"
    assert result["warmup"] == "COMPLETED"


def test_reasoning_change_only_persists_when_qwen_is_not_active(monkeypatch):
    monkeypatch.setattr(model_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        model_routes._local_models,
        "get_local_model_inventory",
        lambda: _inventory(state="READY", active="gemma12"),
    )
    monkeypatch.setattr(
        model_routes._qwen_reasoning,
        "set_reasoning_level",
        lambda level, **_kwargs: {"ok": True, "level": level},
    )
    restart = MagicMock()
    monkeypatch.setattr(model_routes._local_models, "activate_local_model", restart)
    router = _router()
    route = next(
        route
        for route in router.routes
        if route.path == "/api/models/qwen/reasoning-effort" and "POST" in route.methods
    )

    result = route.endpoint(SimpleNamespace(level="low"), SimpleNamespace())

    restart.assert_not_called()
    assert result["restart_scheduled"] is False
    assert result["restart_completed"] is False
    assert result["state"] == "AVAILABLE"
