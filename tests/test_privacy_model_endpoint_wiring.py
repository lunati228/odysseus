"""PRV-009: persisted and helper model URLs are numeric-loopback-only.

The pure validator was present before this file, but production callers did
not use it.  These tests pin the ordering: a remote stored/fallback/helper URL
is refused before credential refresh, DNS/Tailscale resolution, response-cache
lookup, provider probing, or an HTTP client can run.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

import src.endpoint_resolver as endpoint_resolver
import src.llm_core as llm_core
import src.privacy_mode as privacy_mode
from src.privacy_policy import CapabilityDenied


REMOTE = "https://api.example.test/v1"
LOCAL = "http://127.0.0.1:18085/v1"
MESSAGES = [{"role": "user", "content": "hello"}]


@pytest.fixture
def privacy_profile(monkeypatch):
    monkeypatch.setattr(privacy_mode, "PROFILE", "privacy")
    assert privacy_mode.is_privacy_mode()


def test_sync_llm_refuses_remote_url_before_any_http(privacy_profile, monkeypatch):
    monkeypatch.setattr(
        llm_core.httpx,
        "post",
        lambda *_a, **_k: pytest.fail("remote model HTTP was attempted"),
    )

    with pytest.raises(CapabilityDenied):
        llm_core.llm_call(REMOTE, "remote-model", MESSAGES)


@pytest.mark.asyncio
async def test_async_llm_refuses_remote_url_before_building_client(
    privacy_profile, monkeypatch
):
    monkeypatch.setattr(
        llm_core,
        "_get_http_client",
        lambda: pytest.fail("remote async model client was requested"),
    )

    with pytest.raises(CapabilityDenied):
        await llm_core.llm_call_async(REMOTE, "remote-model", MESSAGES)


@pytest.mark.asyncio
async def test_streaming_llm_refuses_remote_url_before_building_client(
    privacy_profile, monkeypatch
):
    monkeypatch.setattr(
        llm_core,
        "_get_http_client",
        lambda: pytest.fail("remote stream client was requested"),
    )

    with pytest.raises(CapabilityDenied):
        async for _chunk in llm_core.stream_llm(REMOTE, "remote-model", MESSAGES):
            pytest.fail("remote model stream produced output")


def test_model_listing_refuses_remote_url_before_cached_rows_or_http(
    privacy_profile, monkeypatch
):
    monkeypatch.setattr(
        llm_core,
        "_configured_cached_model_ids",
        lambda *_a, **_k: pytest.fail("remote cached endpoint was consulted"),
    )
    monkeypatch.setattr(
        llm_core.httpx,
        "get",
        lambda *_a, **_k: pytest.fail("remote model listing was attempted"),
    )

    with pytest.raises(CapabilityDenied):
        llm_core.list_model_ids(REMOTE)


def test_low_level_model_helper_refuses_remote_url_before_http(
    privacy_profile, monkeypatch
):
    monkeypatch.setattr(
        llm_core.httpx,
        "post",
        lambda *_a, **_k: pytest.fail("low-level remote POST was attempted"),
    )

    with pytest.raises(CapabilityDenied):
        llm_core.httpx_post_kimi_aware(REMOTE, {}, json={})


def test_stored_endpoint_is_refused_before_provider_credential_refresh(
    privacy_profile, monkeypatch
):
    import src.chatgpt_subscription as subscription

    monkeypatch.setattr(
        subscription,
        "resolve_runtime_credentials",
        lambda *_a, **_k: pytest.fail("cloud credentials were refreshed"),
    )
    stored = SimpleNamespace(
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=None,
        provider_auth_id="stored-auth",
    )

    with pytest.raises(CapabilityDenied):
        endpoint_resolver.resolve_endpoint_runtime(stored, owner="alice")


def test_fallback_endpoint_is_refused_before_settings_resolution(
    privacy_profile, monkeypatch
):
    import src.settings as settings

    monkeypatch.setattr(
        settings,
        "load_settings",
        lambda: pytest.fail("settings were read before fallback validation"),
    )

    with pytest.raises(CapabilityDenied):
        endpoint_resolver.resolve_endpoint(
            "research",
            fallback_url=REMOTE,
            fallback_model="remote-model",
        )


def test_dns_tailscale_resolution_refuses_remote_url_before_lookup(
    privacy_profile, monkeypatch
):
    monkeypatch.setattr(
        endpoint_resolver,
        "_resolve_tailscale_host",
        lambda *_a, **_k: pytest.fail("DNS/Tailscale lookup was attempted"),
    )

    with pytest.raises(CapabilityDenied):
        endpoint_resolver.resolve_url(REMOTE)


def test_numeric_loopback_still_reaches_sync_model_helper(
    privacy_profile, monkeypatch
):
    request = httpx.Request("POST", f"{LOCAL}/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        json={"choices": [{"message": {"content": "local answer"}}]},
    )
    seen = []

    def fake_post(url, **_kwargs):
        seen.append(url)
        return response

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    assert llm_core.llm_call(LOCAL, "local-model", MESSAGES) == "local answer"
    assert seen == [f"{LOCAL}/chat/completions"]


def test_standard_workspace_keeps_remote_model_behavior(monkeypatch):
    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    request = httpx.Request("POST", f"{REMOTE}/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        json={"choices": [{"message": {"content": "standard answer"}}]},
    )
    monkeypatch.setattr(llm_core.httpx, "post", lambda *_a, **_k: response)

    assert llm_core.llm_call(REMOTE, "remote-model", MESSAGES) == "standard answer"
