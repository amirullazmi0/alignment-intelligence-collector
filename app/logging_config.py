from __future__ import annotations

import copy
import json
import logging
import sys
from datetime import UTC, datetime

RESET = "\033[0m"
LEVEL_COLORS = {
    logging.DEBUG: "\033[34m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33;1m",
    logging.ERROR: "\033[31;1m",
    logging.CRITICAL: "\033[31;1m",
}


class LevelColorFormatter(logging.Formatter):
    def __init__(self, *, use_color: bool) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        formatted_record = copy.copy(record)
        level_label = f"[{record.levelname}]"
        color = LEVEL_COLORS.get(record.levelno)
        formatted_record.levelname = (
            f"{color}{level_label}{RESET}" if self.use_color and color else level_label
        )
        return super().format(formatted_record)


class NdjsonFormatter(logging.Formatter):
    """Satu record menjadi satu baris JSON, siap dikirim sebagai NDJSON.

    Dipakai mode service supaya Nest bisa mem-parse tiap baris log alih-alih menebak
    strukturnya dari teks berformat manusia.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "type": "log",
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def stream_supports_color(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty()) if callable(isatty) else False


def should_use_color(mode: str, stream: object) -> bool:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "always":
        return True
    if normalized_mode == "never":
        return False
    if normalized_mode == "auto":
        return stream_supports_color(stream)
    raise ValueError("LOG_COLOR harus auto, always, atau never")


def configure_service_logging(level: str) -> None:
    """Logging untuk mode service HTTP.

    Sengaja tidak memakai `configure_logging()`: fungsi itu memanggil
    `root.handlers.clear()`, yang di dalam proses uvicorn ikut menghapus handler milik
    server dan membungkam lognya. Di sini handler dipasang pada logger paket saja, dan
    propagasi dimatikan supaya baris tidak tercetak dua kali lewat root.
    """
    logger = logging.getLogger("app")
    for existing in tuple(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(LevelColorFormatter(use_color=should_use_color("auto", handler.stream)))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level))
    logger.propagate = False


def configure_logging(level: str, color_mode: str, *, verbose: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        LevelColorFormatter(use_color=should_use_color(color_mode, handler.stream))
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else getattr(logging, level))
