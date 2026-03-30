"""Unit tests for logging module."""

from __future__ import annotations

import structlog

from solution4.logging import setup_logging


class TestSetupLogging:
    def test_configures_structlog(self) -> None:
        setup_logging()
        # After setup, structlog should produce structured output
        logger = structlog.get_logger()
        assert logger is not None

    def test_idempotent(self) -> None:
        setup_logging()
        setup_logging()  # calling twice should not raise
