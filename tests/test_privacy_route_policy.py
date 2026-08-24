"""PRV-003: direct HTTP entry points obey the central privacy authority.

Startup suppression is not enough: an authenticated caller can still invoke a
disabled integration manually.  These tests exercise the ASGI boundary and
also assert that local-only workspace functions remain reachable.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.privacy_routes import PrivacyRoutePolicyMiddleware


DENIED_ROUTES = (
    ("POST", "/api/shell/exec"),
    ("GET", "/api/cookbook/gpus"),
    ("POST", "/api/model/download"),
    ("POST", "/api/model/serve"),
    ("GET", "/api/mcp/servers"),
    ("POST", "/api/webhooks"),
    ("POST", "/api/v1/chat"),
    ("GET", "/api/email/list"),
    ("POST", "/api/copilot/start"),
    ("POST", "/api/chatgpt-subscription/start"),
    ("POST", "/api/tasks/task-1/run"),
    ("GET", "/api/assistant/session"),
    ("GET", "/api/codex/capabilities"),
    ("GET", "/api/claude/plugin.zip"),
    ("GET", "/api/companion/pair"),
    ("POST", "/api/vault/unlock"),
    ("POST", "/api/auth/integrations/integration-1/test"),
    ("GET", "/api/calendar/config"),
    ("POST", "/api/calendar/test"),
    ("POST", "/api/calendar/sync"),
    ("PUT", "/api/contacts/config"),
    ("GET", "/api/discover"),
    ("POST", "/api/skills/import-from-url"),
    ("POST", "/api/embeddings/models/vendor/model/download"),
)

LOCAL_ROUTES = (
    ("GET", "/api/privacy/status"),
    ("POST", "/api/chat"),
    ("POST", "/api/research/start"),
    ("GET", "/api/models"),
    ("GET", "/api/local-models"),
    ("POST", "/api/local-models/qwen/activation"),
    ("POST", "/api/models/qwen/reasoning-effort"),
    ("GET", "/api/model-endpoints"),
    ("GET", "/api/calendar/events"),
    ("POST", "/api/calendar/events"),
    ("GET", "/api/contacts/list"),
    ("POST", "/api/contacts/add"),
    ("POST", "/api/embeddings/endpoint"),
    ("GET", "/api/emoji/1f600.svg"),
    ("POST", "/api/gallery/upload"),
)


def _client(profile: str) -> tuple[TestClient, list[tuple[str, str]]]:
    app = FastAPI()
    reached: list[tuple[str, str]] = []

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def trap(path: str):
        # The path value is only test bookkeeping; production denial responses
        # intentionally contain neither path nor request content.
        reached.append(("reached", path))
        return {"ok": True}

    app.add_middleware(PrivacyRoutePolicyMiddleware, profile=profile)
    return TestClient(app), reached


@pytest.mark.parametrize(("method", "path"), DENIED_ROUTES)
def test_privacy_route_boundary_denies_manual_entry_points(method, path):
    client, reached = _client("privacy")
    response = client.request(method, path, content=b"PRIVATE_CANARY")

    assert response.status_code == 403
    assert reached == []
    body = response.json()
    assert set(body) == {"detail"}
    assert "privacy" in body["detail"]
    assert path not in body["detail"]
    assert "PRIVATE_CANARY" not in body["detail"]


@pytest.mark.parametrize(("method", "path"), LOCAL_ROUTES)
def test_privacy_route_boundary_keeps_local_functions_reachable(method, path):
    client, reached = _client("privacy")
    response = client.request(method, path)

    assert response.status_code == 200
    assert len(reached) == 1


@pytest.mark.parametrize(("method", "path"), DENIED_ROUTES)
def test_standard_workspace_route_authority_is_unchanged(method, path):
    client, reached = _client("standard")
    response = client.request(method, path)

    assert response.status_code == 200
    assert len(reached) == 1


def test_route_prefix_matching_observes_component_boundaries():
    client, reached = _client("privacy")

    assert client.get("/api/shellfish").status_code == 200
    assert client.get("/api/calendar/configuration").status_code == 200
    assert len(reached) == 2


def test_only_the_embedding_download_action_is_blocked():
    client, reached = _client("privacy")

    assert client.get("/api/embeddings/models/vendor/model/status").status_code == 200
    assert client.delete("/api/embeddings/models/vendor/model").status_code == 200
    assert len(reached) == 2
