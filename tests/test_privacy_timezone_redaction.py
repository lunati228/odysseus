from datetime import datetime, timezone
from types import SimpleNamespace


def test_privacy_datetime_prompt_is_utc_only(monkeypatch):
    import src.privacy_mode as privacy_mode
    import src.user_time as user_time

    monkeypatch.setattr(privacy_mode, "PROFILE", "privacy")
    user_time.clear_user_time_context()
    user_time.set_user_tz_offset(120)
    user_time.set_user_tz_name("Europe/Berlin")

    prompt = user_time.current_datetime_prompt(
        datetime(2026, 8, 25, 3, 30, tzinfo=timezone.utc)
    )

    assert "Europe/Berlin" not in prompt
    assert "UTC+02:00" not in prompt
    assert "User local time" not in prompt
    assert "current UTC time is 03:30" in prompt
    assert "Treat relative dates and times as UTC" in prompt


def test_privacy_chat_request_ignores_browser_timezone_headers(monkeypatch):
    import src.privacy_mode as privacy_mode
    import src.user_time as user_time
    from routes.chat_routes import _set_user_time_from_request

    monkeypatch.setattr(privacy_mode, "PROFILE", "privacy")
    user_time.clear_user_time_context()
    request = SimpleNamespace(
        headers={"x-tz-offset": "120", "x-tz-name": "Europe/Berlin"}
    )

    _set_user_time_from_request(request)

    assert user_time.get_user_tz_offset() is None
    assert user_time.get_user_tz_name() is None
    assert user_time.user_timezone() is timezone.utc
