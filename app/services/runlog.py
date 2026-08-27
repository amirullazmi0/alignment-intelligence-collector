from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from ..logging_config import NdjsonFormatter

PACKAGE_LOGGER = "app"


class QueueLogHandler(logging.Handler):
    """Menyalurkan log record ke sebuah `asyncio.Queue` sebagai baris NDJSON.

    Dipakai mode service untuk menyiarkan jalannya run ke pemanggil HTTP secara langsung,
    tanpa mengubah handler stderr yang sudah terpasang.

    Antreannya sengaja dibatasi dan record dibuang saat penuh: konsumen HTTP yang lambat
    atau menggantung tidak boleh menahan proses crawl. Jumlah yang terbuang dicatat di
    `dropped` agar bisa dilaporkan di akhir run.

    Asumsi: collector berjalan single-thread di atas satu event loop asyncio, sehingga
    `put_nowait` boleh dipanggil langsung dari `emit()`. Kalau suatu saat ada logging dari
    thread lain, ganti dengan `loop.call_soon_threadsafe`.
    """

    def __init__(self, queue: asyncio.Queue[str | None]) -> None:
        super().__init__()
        self._queue = queue
        self.dropped = 0
        self.setFormatter(NdjsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except asyncio.QueueFull:
            self.dropped += 1
        except Exception:
            # Kontrak logging.Handler: kegagalan logging tidak boleh menjatuhkan run.
            self.handleError(record)


@contextmanager
def capture_run_logs(
    queue: asyncio.Queue[str | None], level: str = "INFO"
) -> Iterator[QueueLogHandler]:
    """Pasang `QueueLogHandler` pada logger paket selama satu run.

    Handler dipasang di logger `app`, bukan di root. Dua alasannya: root
    juga memuat handler milik uvicorn dan httpx sehingga stream akan penuh derau, dan
    memasang di paket berarti handler stderr yang sudah ada tetap hidup berdampingan.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    handler = QueueLogHandler(queue)
    handler.setLevel(getattr(logging, level))
    previous_level = logger.level
    logger.addHandler(handler)
    if previous_level == logging.NOTSET or previous_level > handler.level:
        logger.setLevel(handler.level)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


__all__ = ["QueueLogHandler", "capture_run_logs"]
