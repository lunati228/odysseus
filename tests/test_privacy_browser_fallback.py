"""Privacy Workspace Tor-to-Brave fallback state-machine regressions."""

from pathlib import Path


def test_failed_tor_call_unlocks_browser_and_blocks_identical_retry():
    from src.agent_loop import _PrivacyWebFallbackState

    state = _PrivacyWebFallbackState(browser_explicit=False)
    tor_call = "https://example.invalid/page"

    assert state.browser_available is False
    assert state.preflight("web_fetch", tor_call) is None

    result = {"error": "Tor endpoint returned 403", "exit_code": 1}
    state.record_result("web_fetch", tor_call, result)

    assert state.browser_available is True
    assert "browser_navigate" in result["privacy_fallback"]
    retry = state.preflight("web_fetch", tor_call)
    assert retry["exit_code"] == 1
    assert retry["privacy_guard"] == "repeated_tor_call"


def test_browser_fallback_blocks_only_repeated_identical_calls():
    from src.agent_loop import _PrivacyWebFallbackState

    state = _PrivacyWebFallbackState(browser_explicit=False)
    state.record_result(
        "web_fetch",
        "https://example.invalid/page",
        {"error": "Tor failed", "exit_code": 1},
    )
    navigate = '{"url":"https://example.invalid/page","waitUntil":"load"}'

    assert state.preflight(
        "mcp__builtin_browser__browser_navigate", navigate
    ) is None
    state.record_result(
        "mcp__builtin_browser__browser_navigate",
        navigate,
        {"output": "snapshot", "exit_code": 0},
    )

    repeated = state.preflight(
        "mcp__builtin_browser__browser_navigate",
        ' { "waitUntil": "load", "url": "https://example.invalid/page" } ',
    )
    assert repeated["privacy_guard"] == "repeated_browser_call"
    assert state.preflight(
        "mcp__builtin_browser__browser_snapshot", "{}"
    ) is None


def test_explicit_browser_request_does_not_require_a_tor_failure_first():
    from src.agent_loop import _PrivacyWebFallbackState

    state = _PrivacyWebFallbackState(browser_explicit=True)

    assert state.browser_available is True
    assert state.preflight(
        "mcp__builtin_browser__browser_navigate",
        '{"url":"https://example.invalid"}',
    ) is None


def test_fallback_state_is_wired_into_offer_and_execution_paths():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "agent_loop.py"
    ).read_text(encoding="utf-8")

    assert "_privacy_web_fallback.browser_available" in source
    assert "_privacy_web_fallback.preflight(" in source
    assert "_privacy_web_fallback.record_result(" in source
