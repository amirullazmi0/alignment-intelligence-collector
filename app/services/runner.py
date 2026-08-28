from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from ..config import CollectorSettings
from ..domain.ministries import find_ministry
from ..domain.models import RunRequest
from ..storage.repository import CollectorRepository, Run
from ..storage.state import StateStore
from .collector import CollectorService
from .runlog import capture_run_logs

LOGGER = logging.getLogger(__name__)

QUEUE_MAXSIZE = 1000
LOG_FLUSH_SIZE = 25
HEARTBEAT_SECONDS = 20.0

INTERRUPTED_MESSAGE = (
    "Run terputus ketika service collector berhenti dan tidak pernah dilanjutkan. "
    "Antrean eksekutor hanya hidup di memori, jadi run yang sedang berjalan tidak "
    "bisa disambung kembali setelah restart."
)


class RunBroker:
    """Penyiar kejadian run ke penonton yang sedang terhubung.

    Seluruh aplikasi berjalan dalam satu proses, jadi tidak perlu Redis: cukup daftar
    `asyncio.Queue` per run. Penonton yang lambat tidak boleh menahan crawl, karena itu
    antreannya dibatasi dan kejadian dibuang saat penuh.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
        # Penonton daftar riwayat: menerima perubahan status seluruh run, bukan satu run
        # tertentu, supaya halaman daftar ikut bergerak tanpa polling.
        self._watchers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        watchers = self._subscribers.get(run_id)
        if not watchers:
            return
        watchers.discard(queue)
        if not watchers:
            self._subscribers.pop(run_id, None)

    def subscribe_all(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._watchers.add(queue)
        return queue

    def unsubscribe_all(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._watchers.discard(queue)

    def publish(self, run_id: str, event: dict[str, Any] | None) -> None:
        for queue in tuple(self._subscribers.get(run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                LOGGER.debug("Antrean penonton penuh, satu kejadian dibuang")

        # Baris log tidak disiarkan ke daftar riwayat: yang ditunggu di sana hanya
        # perubahan status, dan mengirim tiap baris hanya akan membanjiri koneksi.
        if event is None or event.get("type") != "status":
            return
        for watcher in tuple(self._watchers):
            try:
                watcher.put_nowait({**event, "runId": run_id})
            except asyncio.QueueFull:
                LOGGER.debug("Antrean daftar riwayat penuh, satu kejadian dibuang")


class RunExecutor:
    """Antrean run dengan satu pekerja.

    Satu run pada satu waktu bukan pilihan gaya, melainkan keharusan: JDIHN mereset
    koneksi kalau diberondong, dan handler log dipasang pada logger paket yang dipakai
    bersama sehingga dua run bersamaan akan saling mencampur keluarannya. Jadwal cron yang
    kebetulan jatuh di menit yang sama akan mengantre, bukan saling menjatuhkan.
    """

    def __init__(
        self,
        settings: CollectorSettings,
        state: StateStore,
        repository: CollectorRepository,
        broker: RunBroker,
    ) -> None:
        self._settings = settings
        self._state = state
        self._repository = repository
        self._broker = broker
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._current_run_id: str | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._consume())
            LOGGER.info("Antrean run collector siap")

    async def recover(self) -> tuple[int, int]:
        """Sambung kembali keadaan database dengan antrean yang baru kosong.

        Antrean ini hidup di memori, jadi restart menghapusnya sementara barisnya
        tetap ada di database. Sebelum rekonsiliasi ini, run yang terputus
        menggantung RUNNING selamanya dan run yang belum sempat diambil menumpuk
        sebagai QUEUED yang tak pernah dieksekusi siapa pun.

        Kembalikan (jumlah run yang digagalkan, jumlah run yang diantre ulang).
        """
        interrupted = await self._repository.recover_interrupted_runs(INTERRUPTED_MESSAGE)
        if interrupted:
            LOGGER.warning("Run yang terputus oleh restart ditandai gagal: %s", interrupted)
        pending = await self._repository.pending_run_ids()
        for run_id in pending:
            await self._queue.put(run_id)
        if pending:
            LOGGER.info("Run tertunda dimuat ulang ke antrean: %s", len(pending))
        return interrupted, len(pending)

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    @property
    def queue_length(self) -> int:
        return self._queue.qsize()

    @property
    def current_run_id(self) -> str | None:
        return self._current_run_id

    async def enqueue(self, run_id: str) -> int:
        await self._queue.put(run_id)
        return self._queue.qsize()

    async def _consume(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await self._execute(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Run collector gagal di luar dugaan run_id=%s", run_id)
            finally:
                self._queue.task_done()

    async def _execute(self, run_id: str) -> None:
        run = await self._repository.get_run(run_id)
        if run is None:
            LOGGER.warning("Run tidak ditemukan, dilewati run_id=%s", run_id)
            return

        self._current_run_id = run_id
        await self._repository.start_run(run_id)
        self._broker.publish(run_id, {"type": "status", "status": "RUNNING"})

        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        request = _request_for(self._settings, run)
        pending: list[dict[str, Any]] = []

        async def flush() -> None:
            if not pending:
                return
            await self._repository.append_logs(run_id, pending)
            pending.clear()

        try:
            with capture_run_logs(queue, self._settings.log_level):
                task = asyncio.create_task(
                    CollectorService(self._settings, self._state).run(request)
                )
                task.add_done_callback(lambda _t: _offer(queue, None))
                while True:
                    line = await queue.get()
                    if line is None:
                        break
                    event: dict[str, Any] = json.loads(line)
                    event["ts"] = _parse_ts(event.get("ts"))
                    self._broker.publish(run_id, {**event, "ts": event["ts"].isoformat()})
                    pending.append(event)
                    if len(pending) >= LOG_FLUSH_SIZE:
                        await flush()
            await flush()
            summary = task.result()
            await self._repository.finish_run(run_id, summary)
            self._broker.publish(
                run_id, {"type": "status", "status": "SUCCEEDED", "summary": summary.as_dict()}
            )
        except Exception as error:
            await flush()
            message = str(error).strip() or type(error).__name__
            await self._repository.fail_run(run_id, message)
            self._broker.publish(
                run_id, {"type": "status", "status": "FAILED", "errorMessage": message}
            )
            LOGGER.exception("Run collector gagal run_id=%s", run_id)
        finally:
            self._current_run_id = None
            # Sinyal penutup supaya stream SSE milik penonton ikut berakhir.
            self._broker.publish(run_id, None)


def _offer(queue: asyncio.Queue[str | None], line: str | None) -> None:
    try:
        queue.put_nowait(line)
    except asyncio.QueueFull:
        LOGGER.debug("Antrean log penuh, satu baris dibuang")


def _parse_ts(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)


def _request_for(settings: CollectorSettings, run: Run) -> RunRequest:
    # Run general tidak menyasar kementerian mana pun: keyword-nya sendiri yang
    # menentukan cakupan, dan pencocokan instansi dilewati sepenuhnya.
    if run.ministry is None:
        return RunRequest.for_keywords(
            settings,
            "backfill",
            dry_run=run.dry_run,
            limit=run.document_limit,
            keywords=tuple(run.keywords),
        )

    ministry = find_ministry(run.ministry)
    return RunRequest.for_ministry(
        settings,
        "backfill",
        dry_run=run.dry_run,
        limit=run.document_limit,
        ministry=run.ministry,
        aliases=ministry.aliases if ministry else (),
        extra_keywords=tuple(k for k in run.keywords if k != run.ministry),
    )


async def stream_run_feed(broker: RunBroker) -> AsyncIterator[bytes]:
    """Siarkan perubahan status seluruh run untuk halaman daftar riwayat.

    Denyut berkala dikirim supaya proxy tidak memutus koneksi yang lama sunyi -- daftar
    riwayat bisa diam berjam-jam sampai cron berikutnya menyala.
    """
    queue = broker.subscribe_all()
    try:
        yield _encode({"type": "ready"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                # Komentar SSE: menjaga koneksi tetap hidup tanpa memancing pemuatan ulang.
                yield b": ping\n\n"
                continue
            yield _encode(event)
    finally:
        broker.unsubscribe_all(queue)


async def stream_events(
    broker: RunBroker, run_id: str, replay: list[dict[str, object]]
) -> AsyncIterator[bytes]:
    """Kirim riwayat log lebih dulu, baru kejadian berikutnya.

    Riwayat dikirim sebagai satu `snapshot`; sisi frontend harus memperlakukannya sebagai
    pengganti isi, bukan tambahan, kalau tidak log akan berganda tiap kali tersambung ulang.
    """
    queue = broker.subscribe(run_id)
    try:
        yield _encode({"type": "snapshot", "lines": replay})
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _encode(event)
    finally:
        broker.unsubscribe(run_id, queue)


def _encode(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


__all__ = ["RunBroker", "RunExecutor", "stream_events", "stream_run_feed"]
