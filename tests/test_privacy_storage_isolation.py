"""PRV-005: the privacy path writes no plaintext analytics and no disk cache.

The previous evidence for this was a log canary -- a live run, then a grep of
the vault for the test query. That is necessary but not sufficient: it proves
nothing was written *that time*, on *that path*. These tests prove the writers
are unreachable by construction.

The defect this file was written against was real. The privacy guard sits
inside ``services.search.core._call_provider``, which replaces only the leaf
network call. Its caller ``searxng_search_results`` kept running the disk
search cache and ``_record_query`` around it -- and ``_record_query`` stores
the raw query string as a key in ``search_analytics.json``. The canary missed
it because that file is ``.json``, not ``.log``, and because the live run
exercised the transport directly rather than through the orchestrator.
"""
from __future__ import annotations

import importlib
import json
import logging

import pytest

import src.privacy_mode as privacy_mode
from src.privacy_policy import CapabilityDenied


@pytest.fixture
def privacy_profile(monkeypatch):
    """Run the body as if the process had started with ODYSSEUS_PROFILE=privacy.

    ``is_privacy_mode()`` reads the module global on every call, so patching it
    is enough and no re-import is required.
    """
    monkeypatch.setattr(privacy_mode, "PROFILE", "privacy")
    assert privacy_mode.is_privacy_mode() is True
    yield


# ---------------------------------------------------------------------------
# search analytics
# ---------------------------------------------------------------------------


def test_analytics_are_on_in_standard_and_off_in_privacy(privacy_profile, monkeypatch):
    from services.search import analytics

    assert analytics.analytics_enabled() is False
    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    assert analytics.analytics_enabled() is True


def test_recording_a_query_writes_nothing_in_privacy(privacy_profile, tmp_path, monkeypatch):
    from services.search import analytics

    target = tmp_path / "search_analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", target)

    analytics._record_query("a private medical question", success=True, cache_hit=False)

    assert not target.exists()


def test_the_analytics_writer_itself_refuses_in_privacy(privacy_profile, tmp_path, monkeypatch):
    """The backstop: a future caller that forgets the check still cannot write."""
    from services.search import analytics

    monkeypatch.setattr(analytics, "ANALYTICS_FILE", tmp_path / "x.json")
    with pytest.raises(CapabilityDenied):
        analytics._save_analytics(analytics._default_analytics())


def test_loading_analytics_in_privacy_returns_defaults_without_touching_disk(
    privacy_profile, tmp_path, monkeypatch
):
    """get_search_stats() must keep working without creating the file."""
    from services.search import analytics

    target = tmp_path / "search_analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", target)

    assert analytics._load_analytics() == analytics._default_analytics()
    assert analytics.get_search_stats()["total_queries"] == 0
    assert not target.exists()


def test_the_standard_profile_still_records_queries(tmp_path, monkeypatch):
    """Regression guard: this must not quietly disable standard analytics."""
    from services.search import analytics

    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    target = tmp_path / "search_analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", target)

    analytics._record_query("ordinary query", success=True, cache_hit=False)

    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["total_queries"] == 1
    assert "ordinary query" in stored["query_patterns"]


def test_the_search_error_log_has_no_file_sink_in_privacy(privacy_profile):
    """Its call sites format the fetched URL into the message.

    ``core.py``, ``content.py`` and ``providers.py`` all log
    ``f"... {url}: {e}"``, and ``PrivacyTransportError`` messages embed the URL
    too, so a file handler here would put research targets at rest inside the
    vault. Re-imported under the privacy profile to observe what the module
    actually attaches at import time.
    """
    logger = logging.getLogger("search_engine_error")
    saved = list(logger.handlers)
    logger.handlers.clear()
    try:
        module = importlib.reload(importlib.import_module("services.search.analytics"))
        assert module.analytics_enabled() is False
        assert logger.propagate is False
        assert not [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    finally:
        logger.handlers.clear()
        logger.handlers.extend(saved)
        # Restore the module to the profile the rest of the session runs under.
        importlib.reload(importlib.import_module("services.search.analytics"))


# ---------------------------------------------------------------------------
# the on-disk web cache
# ---------------------------------------------------------------------------


def test_the_disk_cache_is_off_in_privacy_and_on_in_standard(privacy_profile, monkeypatch):
    from services.search import cache

    assert cache.disk_cache_enabled() is False
    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    assert cache.disk_cache_enabled() is True


def test_the_disk_cache_writer_refuses_in_privacy(privacy_profile):
    from services.search import cache

    with pytest.raises(CapabilityDenied):
        cache.require_disk_cache()


def test_the_content_cache_writer_refuses_in_privacy(privacy_profile, tmp_path):
    from services.search import content

    with pytest.raises(CapabilityDenied):
        content._cache_result(
            tmp_path / "entry.cache", "key", {"content": "page text"}, "https://x.test/"
        )


def test_a_privacy_search_writes_no_cache_entry_and_no_analytics(
    privacy_profile, tmp_path, monkeypatch
):
    """End to end through the orchestrator -- the path the canary missed."""
    from services.search import analytics, core

    cache_dir = tmp_path / "search"
    cache_dir.mkdir()
    analytics_file = tmp_path / "search_analytics.json"

    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", cache_dir)
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", analytics_file)
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "duckduckgo"})
    monkeypatch.setattr(core, "_get_result_count", lambda: 5)
    monkeypatch.setattr(core, "rank_search_results", lambda _query, results: results)
    monkeypatch.setattr(
        core,
        "_call_provider",
        lambda *_a, **_k: [{"title": "t", "url": "https://x.test/", "snippet": "s"}],
    )

    results = core.searxng_search_results("a private medical question", count=5)

    assert results, "the search itself must still work"
    assert list(cache_dir.iterdir()) == [], "a disk cache entry was written"
    assert not analytics_file.exists(), "the plaintext analytics file was written"


def test_the_standard_profile_still_writes_its_cache_and_analytics(tmp_path, monkeypatch):
    """The other half of the regression guard."""
    from services.search import analytics, core

    monkeypatch.setattr(privacy_mode, "PROFILE", "standard")
    cache_dir = tmp_path / "search"
    cache_dir.mkdir()
    analytics_file = tmp_path / "search_analytics.json"

    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", cache_dir)
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", analytics_file)
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "duckduckgo"})
    monkeypatch.setattr(core, "_get_result_count", lambda: 5)
    monkeypatch.setattr(core, "rank_search_results", lambda _query, results: results)
    monkeypatch.setattr(
        core,
        "_call_provider",
        lambda *_a, **_k: [{"title": "t", "url": "https://x.test/", "snippet": "s"}],
    )

    core.searxng_search_results("ordinary query", count=5)

    assert list(cache_dir.glob("*.cache")), "the standard disk cache stopped working"
    assert analytics_file.exists(), "standard analytics stopped being recorded"


def test_the_privacy_profile_does_not_even_create_the_cache_directories(privacy_profile):
    """Empty cache directories inside the vault would misrepresent the design."""
    module = importlib.reload(importlib.import_module("services.search.cache"))
    try:
        assert module.disk_cache_enabled() is False
        assert not module.SEARCH_CACHE_DIR.exists() or not any(
            module.SEARCH_CACHE_DIR.iterdir()
        )
    finally:
        importlib.reload(importlib.import_module("services.search.cache"))
