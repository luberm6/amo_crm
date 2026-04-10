"""
Structured logging setup using structlog.
In development: human-readable console output with colors.
In production: JSON output suitable for log aggregators (Datadog, Loki, etc.).
Usage:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.info("call_created", call_id=str(call.id), phone=call.phone)
"""
from __future__ import annotations
import logging
import sys
import structlog
from app.core.config import settings
def setup_logging() -> None:
    """Configure structlog and stdlib logging. Call once at app startup."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if settings.log_format == "json":
        # Production: machine-readable JSON
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: colorful console
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())
    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)