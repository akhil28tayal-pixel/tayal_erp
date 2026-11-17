import logging

import structlog


def configure_logging(env: str) -> None:
    """JSON logs in deployed environments, readable console logs locally."""
    renderer = (
        structlog.dev.ConsoleRenderer() if env in {"local", "test"} else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING if env == "test" else logging.INFO),
    )
