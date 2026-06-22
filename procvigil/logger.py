"""Loglama yardımcıları.

İki tür log vardır:

1. Daemon logu  -> ProcVigil'ın kendi olayları (start/stop/restart/health vb.).
   systemd altında çalışırken stdout'a yazmak yeterlidir; journald yakalar.
2. Program logu  -> her gözetilen sürecin stdout/stderr çıktısı. Bunlar
   ayrı dosyalara, boyut tabanlı döndürme (rotation) ile yazılır.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_daemon_logger(loglevel: str = "info") -> logging.Logger:
    """ProcVigil daemon logger'ını stdout'a yapılandırır (journald-dostu)."""
    logger = logging.getLogger("procvigil")
    logger.setLevel(_LEVELS.get(loglevel, logging.INFO))
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def open_program_logfile(
    path: str,
    maxbytes: int,
    backups: int,
) -> RotatingFileHandler:
    """Bir program çıktısı için döndürülen (rotating) dosya handler'ı açar."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=maxbytes,
        backupCount=backups,
        encoding="utf-8",
    )
    # Süreç çıktısını olduğu gibi yaz; ekstra format ekleme.
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def make_program_logger(name: str, path: str, maxbytes: int, backups: int) -> logging.Logger:
    """Belirli bir program akışı (stdout/stderr) için izole logger üretir."""
    logger = logging.getLogger(f"procvigil.program.{name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(open_program_logfile(path, maxbytes, backups))
    logger.propagate = False
    return logger
