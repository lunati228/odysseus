from routes.chat_routes import _coerce_agent_max_rounds


def test_zero_agent_round_limit_means_unlimited():
    assert _coerce_agent_max_rounds(0, default=50) == 0


def test_agent_round_limit_is_bounded_and_invalid_values_use_default():
    assert _coerce_agent_max_rounds(999, default=50) == 200
    assert _coerce_agent_max_rounds("invalid", default=50) == 50
