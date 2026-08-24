"""Process-wide metadata-only logging for the Privacy Workspace.

The installed Windows manager redirects both stdout and stderr to files inside
the private vault.  Consequently, removing the application's ``app.log``
handler would not prevent a search query, research plan, URL, model response,
or exception message from being persisted.  The privacy profile instead
sanitizes every Python ``LogRecord`` when it is created, before any console or
file handler can format it.

Only the logger name, severity, timestamp, and source metadata remain useful.
The message, interpolation arguments, exception text, and stack text are
discarded.  Uvicorn's access logger is disabled because its specialized
formatter unpacks request details from ``record.args`` and those details can
include a query string.  Standard Workspace logging is not changed.

This controls Python logging only.  Native crash capture, Windows Error
Reporting, and output written directly to stdout/stderr remain separate
acceptance items under PRV-012.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.privacy_mode import is_privacy_mode


PRIVACY_LOG_MESSAGE = "privacy event details suppressed"

_installed = False
_original_factory: Optional[Callable[..., logging.LogRecord]] = None
_uvicorn_access_was_disabled: Optional[bool] = None


def _sanitize_record(record: logging.LogRecord) -> logging.LogRecord:
    """Remove fields that can carry user, research, URL, or exception text."""
    record.msg = PRIVACY_LOG_MESSAGE
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    return record


def install_privacy_log_sanitizer(*, profile: Optional[str] = None) -> bool:
    """Install the record sanitizer in Privacy Workspace; otherwise no-op.

    The installation is idempotent because ``logging`` owns one process-wide
    factory.  It runs before application/service imports in ``app.py`` so
    module-import warnings are covered too.
    """
    global _installed, _original_factory, _uvicorn_access_was_disabled

    if _installed:
        return False
    if not is_privacy_mode(profile):
        return False

    original = logging.getLogRecordFactory()

    def privacy_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return _sanitize_record(original(*args, **kwargs))

    _original_factory = original
    logging.setLogRecordFactory(privacy_record_factory)

    # Uvicorn's AccessFormatter expects a five-item record.args tuple and
    # embeds the request target. Sanitizing that tuple would make its formatter
    # raise; retaining it would persist private URLs. Refuse the sink entirely.
    access_logger = logging.getLogger("uvicorn.access")
    _uvicorn_access_was_disabled = access_logger.disabled
    access_logger.disabled = True

    _installed = True
    return True


def is_privacy_log_sanitizer_installed() -> bool:
    return _installed


def _uninstall_privacy_log_sanitizer_for_tests() -> bool:
    """Restore global logging state. Test-only; the application never calls it."""
    global _installed, _original_factory, _uvicorn_access_was_disabled

    if not _installed:
        return False
    if _original_factory is not None:
        logging.setLogRecordFactory(_original_factory)
    if _uvicorn_access_was_disabled is not None:
        logging.getLogger("uvicorn.access").disabled = _uvicorn_access_was_disabled
    _original_factory = None
    _uvicorn_access_was_disabled = None
    _installed = False
    return True
