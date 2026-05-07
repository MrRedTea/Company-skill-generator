"""Application logging helpers for 算疏智合.

This module centralizes file logging so the GUI can keep its visual console hidden
while developers and users still have durable diagnostics under ./logs.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"
ERROR_LOG_PATH = LOG_DIR / "error.log"


def setup_app_logging(app_name: str = "算疏智合") -> logging.Logger:
    """Return a configured rotating application logger.

    Safe to call multiple times; handlers are only attached once.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("suanshu_zhihe")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if getattr(logger, "_suanshu_configured", False):
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    app_handler = RotatingFileHandler(
        APP_LOG_PATH,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        ERROR_LOG_PATH,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logger.addHandler(app_handler)
    logger.addHandler(error_handler)
    logger._suanshu_configured = True  # type: ignore[attr-defined]
    logger.info("%s logger initialized", app_name)
    return logger


def create_task_log_path(prefix: str = "generation") -> Path:
    """Create a timestamped per-task log path under ./logs."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"{prefix}-{stamp}.log"


def append_task_log(path: Path | None, line: str) -> None:
    """Append one line to a per-task log file without raising GUI-facing errors."""
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")
    except Exception:
        # Logging must never crash the GUI.
        return
