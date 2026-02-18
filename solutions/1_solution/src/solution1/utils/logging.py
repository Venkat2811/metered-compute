from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars


def configure_logging() -> None:
    """Configure structured JSON logging for all runtime services."""

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = False


def bind_log_context(**kwargs: object) -> None:
    bind_contextvars(**kwargs)


def clear_log_context() -> None:
    clear_contextvars()


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a bound structured logger."""

    return cast(structlog.BoundLogger, structlog.get_logger(name))
