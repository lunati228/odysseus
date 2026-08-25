from src.research_handler import (
    _research_generation_tokens,
    _resolve_research_hard_timeout,
    _resolve_research_max_time,
)


def test_research_uses_unlimited_setting_when_caller_does_not_override():
    assert _resolve_research_max_time(None, configured=0) == 0


def test_research_uses_configured_positive_runtime_when_not_overridden():
    assert _resolve_research_max_time(None, configured=3600) == 3600


def test_research_explicit_runtime_still_wins():
    assert _resolve_research_max_time(600, configured=0) == 600


def test_explicit_zero_disables_the_hard_timeout_too():
    assert _resolve_research_hard_timeout(0, configured=1800) is None


def test_inherited_zero_disables_the_hard_timeout():
    assert _resolve_research_hard_timeout(None, configured=0) is None


def test_positive_research_hard_timeout_is_bounded():
    assert _resolve_research_hard_timeout(None, configured=3600) == 3600
    assert _resolve_research_hard_timeout(999_999, configured=3600) == 86_400


def test_research_runtime_normalization_is_bounded_and_defensive():
    assert _resolve_research_max_time(None, configured="invalid") == 1800
    assert _resolve_research_max_time(-10, configured=3600) == 0
    assert _resolve_research_max_time(999_999, configured=3600) == 86_400


def test_zero_research_token_setting_does_not_cap_reasoning_calls():
    assert _research_generation_tokens(200, configured=0) == 0


def test_positive_research_token_setting_keeps_small_helper_caps():
    assert _research_generation_tokens(200, configured=16_384) == 200
    assert _research_generation_tokens(20_000, configured=16_384) == 16_384
