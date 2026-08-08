"""Logging: Konsole + rotierendes Logfile."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LoggingConfig, log_dir

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(threadName)s %(name)s: %(message)s"


def setup(cfg: LoggingConfig) -> logging.Logger:
    root = logging.getLogger("flowtext")
    root.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    root.handlers.clear()
    root.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(console)

    if cfg.to_file:
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                directory / "flowtext.log",
                maxBytes=cfg.max_bytes,
                backupCount=cfg.backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
            root.addHandler(file_handler)
        except OSError as exc:  # z. B. read-only Home
            root.warning("Logfile konnte nicht angelegt werden: %s", exc)

    return root
