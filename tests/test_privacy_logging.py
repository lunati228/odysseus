"""PRV-005: Privacy Workspace logs are metadata-only.

The installed manager redirects the application's stderr stream into the
private vault.  Disabling only ``app.log`` is therefore insufficient: every
ordinary console log record is still persisted by the launcher.  These tests
exercise the process-wide record boundary so formatted arguments, eager
f-strings, exception messages, and tracebacks cannot carry user/research text
to either sink.
"""
from __future__ import annotations

import logging

import pytest

from src.privacy_logging import (
    PRIVACY_LOG_MESSAGE,
    install_privacy_log_sanitizer,
    is_privacy_log_sanitizer_installed,
    _uninstall_privacy_log_sanitizer_for_tests,
)


@pytest.fixture(autouse=True)
def restore_log_record_factory():
    """The sanitizer is process-wide, so every test must restore it."""
    _uninstall_privacy_log_sanitizer_for_tests()
    yield
    _uninstall_privacy_log_sanitizer_for_tests()


def _file_logger(path):
    logger = logging.getLogger("odysseus.tests.privacy-canary")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(name)s|%(levelname)s|%(message)s|%(stack_info)s")
    )
    logger.addHandler(handler)
    return logger, handler


def test_standard_profile_keeps_log_content_unchanged(tmp_path):
    target = tmp_path / "standard.log"
    logger, handler = _file_logger(target)
    try:
        assert install_privacy_log_sanitizer(profile="standard") is False
        logger.info("ordinary %s", "standard detail")
    finally:
        handler.close()
        logger.handlers.clear()

    assert "ordinary standard detail" in target.read_text(encoding="utf-8")
    assert is_privacy_log_sanitizer_installed() is False


def test_privacy_profile_removes_every_content_bearing_record_field(tmp_path):
    target = tmp_path / "privacy.log"
    logger, handler = _file_logger(target)
    percent_canary = "PERCENT_PRIVATE_QUERY_6f8c"
    eager_canary = "EAGER_PRIVATE_PLAN_8a2d"
    exception_canary = "EXCEPTION_PRIVATE_URL_3c91"
    stack_canary = "STACK_PRIVATE_CONTEXT_f117"

    try:
        assert install_privacy_log_sanitizer(profile="privacy") is True
        logger.info("query=%s", percent_canary)
        logger.warning(f"plan={eager_canary}")
        try:
            raise RuntimeError(exception_canary)
        except RuntimeError:
            logger.exception("research failed")
        logger.error("stack marker %s", stack_canary, stack_info=True)
    finally:
        handler.close()
        logger.handlers.clear()

    written = target.read_text(encoding="utf-8")
    for canary in (
        percent_canary,
        eager_canary,
        exception_canary,
        stack_canary,
        "research failed",
        "RuntimeError",
    ):
        assert canary not in written
    assert written.count(PRIVACY_LOG_MESSAGE) == 4
    assert written.count("odysseus.tests.privacy-canary") == 4
    assert is_privacy_log_sanitizer_installed() is True


def test_install_is_idempotent_and_does_not_wrap_itself_twice():
    before = logging.getLogRecordFactory()
    assert install_privacy_log_sanitizer(profile="privacy") is True
    installed = logging.getLogRecordFactory()
    assert installed is not before
    assert install_privacy_log_sanitizer(profile="privacy") is False
    assert logging.getLogRecordFactory() is installed
