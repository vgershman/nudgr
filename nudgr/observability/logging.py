"""loguru bootstrap. Call `configure_logging()` once at process start."""

from __future__ import annotations

import logging
import sys

from loguru import logger

from nudgr.config import settings

_CONFIGURED = False


class _InterceptHandler(logging.Handler):
    """Route stdlib `logging` records through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "| <level>{message}</level>"
        ),
        backtrace=True,
        diagnose=settings.environment != "production",
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("sqlalchemy.engine", "httpx", "httpcore", "aiogram.event"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _CONFIGURED = True


__all__ = ["configure_logging", "logger"]
