"""Shared logging configuration for platform commands."""

import sys

from loguru import logger


def configure_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        serialize=json_logs,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
