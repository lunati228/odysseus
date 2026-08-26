"""Connection accounting for on-demand Privacy Workspace shutdown."""

from src.privacy_ui_presence import PrivacyUiPresence


def test_presence_tracks_multiple_tabs_without_focus_or_timer_state():
    presence = PrivacyUiPresence()
    first_tab = object()
    second_tab = object()

    presence.connect(first_tab)
    presence.connect(second_tab)

    assert presence.open_tabs == 2

    presence.disconnect(first_tab)
    assert presence.open_tabs == 1

    presence.disconnect(second_tab)
    assert presence.open_tabs == 0


def test_presence_connect_and_disconnect_are_idempotent():
    presence = PrivacyUiPresence()
    tab = object()

    presence.connect(tab)
    presence.connect(tab)
    assert presence.open_tabs == 1

    presence.disconnect(tab)
    presence.disconnect(tab)
    assert presence.open_tabs == 0
