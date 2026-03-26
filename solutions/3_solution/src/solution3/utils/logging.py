from __future__ import annotations

import logging
from typing import cast

import structlog


def configure_logging(*, enable_sensitive: bool = False) -> None:
    """Configure shared stdlib + structlog logging for the service."""

    _ = enable_sensitive
    level = logging.INFO
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a namespaced structured logger."""

    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
