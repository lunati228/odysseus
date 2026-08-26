"""In-memory browser-tab presence for the local Privacy Workspace.

The browser keeps one HTTP stream open per loaded tab. Counting connections,
rather than focus events or JavaScript timer ticks, keeps background tabs alive
without collecting a URL, title, session identifier, or any other user data.
"""
from __future__ import annotations

from threading import RLock
from typing import Hashable


class PrivacyUiPresence:
    """Thread-safe, idempotent accounting for connected browser tabs."""

    def __init__(self) -> None:
        self._connections: set[Hashable] = set()
        self._lock = RLock()

    def connect(self, token: Hashable) -> None:
        with self._lock:
            self._connections.add(token)

    def disconnect(self, token: Hashable) -> None:
        with self._lock:
            self._connections.discard(token)

    @property
    def open_tabs(self) -> int:
        with self._lock:
            return len(self._connections)


privacy_ui_presence = PrivacyUiPresence()


__all__ = ["PrivacyUiPresence", "privacy_ui_presence"]
