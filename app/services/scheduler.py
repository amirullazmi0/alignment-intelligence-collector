from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..storage.repository import CollectorRepository, Schedule
from .catalog_sync import CatalogSync
from .runner import RunExecutor

LOGGER = logging.getLogger(__name__)

JOB_PREFIX = "ministry-schedule:"
CATALOG_JOB_PREFIX = "pgo-catalog:"
#: Built-in jobs of the peraturan.go.id mirror (cron, Asia/Jakarta). Spread over the
#: hour so they rarely wait on each other's lock.
CATALOG_JOBS: dict[str, str] = {
    "sync_new": "5 * * * *",
    # Details every 10 minutes keep ahead of ingest_text, which only sends
    # regulations whose status and relations are known.
    "refresh_details": "*/10 * * * *",
    # Every 5 minutes: tops Alignment's queue up (PGO_QUEUE_TARGET), so a batch
    # follows soon after the previous one is ingested.
    "ingest_text": "*/5 * * * *",
    "sweep": "30 1 * * *",
}


def build_trigger(cron_expression: str, timezone: str) -> CronTrigger:
    """Bangun trigger cron; melempar ValueError kalau ekspresinya tidak sah.

    Dipakai juga oleh API untuk memvalidasi ekspresi sebelum jadwal disimpan, supaya
    ekspresi yang salah ditolak saat dibuat, bukan diam-diam gagal saat waktunya tiba.
    """
    return CronTrigger.from_crontab(cron_expression, timezone=ZoneInfo(timezone))


def staggered_cron(index: int, every_minutes: int) -> str:
    """Ekspresi cron yang menyebar jadwal ke dalam jendela `every_minutes`.

    Kalau seluruh kementerian memakai jadwal yang sama persis, semuanya menyala pada
    detik yang sama lalu menumpuk di antrean sekaligus. Menggeser tiap jadwal membuat
    beban masuk bertahap, dan satu run sudah selesai sebelum berikutnya tiba.

    Periode di bawah satu jam disebar lewat kolom menit; contoh `every_minutes=10`
    memberi indeks 0 menit 0,10,20,30,40,50 dan indeks 1 menit 1,11,21,...

    Periode satu jam ke atas disebar lewat kolom menit juga, tapi kolom jamnya yang
    melangkah; contoh `every_minutes=120` menghasilkan `7 */2 * * *` untuk indeks 7 --
    tiap dua jam, pada menit ke-7.
    """
    if every_minutes < 60:
        window = max(1, every_minutes)
        offset = index % window
        minutes = ",".join(str(minute) for minute in range(offset, 60, window))
        return f"{minutes} * * * *"

    hours = max(1, min(every_minutes // 60, 24))
    minute = index % 60
    if hours == 1:
        return f"{minute} * * * *"
    if hours >= 24:
        return f"{minute} 0 * * *"
    return f"{minute} */{hours} * * *"


def next_fire_time(cron_expression: str, timezone: str) -> datetime | None:
    trigger = build_trigger(cron_expression, timezone)
    now = datetime.now(ZoneInfo(timezone))
    fire_time: datetime | None = trigger.get_next_fire_time(None, now)
    return fire_time


class CollectorScheduler:
    """Menjalankan jadwal cron per kementerian.

    Cron sengaja tidak menjalankan run secara langsung. Ia hanya membuat baris
    `collector_runs` berstatus QUEUED lalu menitipkannya ke `RunExecutor`, yang
    mengerjakannya satu per satu. Dengan begitu 49 jadwal yang kebetulan jatuh bersamaan
    akan mengantre rapi alih-alih membanjiri JDIHN sampai koneksinya diputus.
    """

    def __init__(self, repository: CollectorRepository, executor: RunExecutor) -> None:
        self._repository = repository
        self._executor = executor
        self._scheduler = AsyncIOScheduler()

    async def start(self) -> None:
        self._scheduler.start()
        await self.reload()
        LOGGER.info("Scheduler cron collector aktif")

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def reload(self) -> None:
        """Muat ulang seluruh jadwal aktif dari database.

        Dipanggil saat startup dan setiap kali jadwal berubah lewat API, supaya perubahan
        langsung berlaku tanpa perlu me-restart service.
        """
        for job in self._scheduler.get_jobs():
            if job.id.startswith(JOB_PREFIX):
                job.remove()

        registered = 0
        for schedule in await self._repository.active_schedules():
            try:
                trigger = build_trigger(schedule.cron_expression, schedule.timezone)
            except ValueError as error:
                LOGGER.error(
                    "Ekspresi cron tidak sah untuk %s (%s): %s",
                    schedule.ministry_label,
                    schedule.cron_expression,
                    error,
                )
                continue
            self._scheduler.add_job(
                self._fire,
                trigger=trigger,
                id=f"{JOB_PREFIX}{schedule.id}",
                args=[schedule.id],
                # Jadwal yang tertinggal saat service mati tidak boleh menumpuk lalu
                # meledak jadi banyak run sekaligus ketika service hidup lagi.
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
            await self._repository.set_next_run_at(
                schedule.id, next_fire_time(schedule.cron_expression, schedule.timezone)
            )
            registered += 1
        LOGGER.info("Jadwal cron aktif: %s", registered)

    def register_catalog_jobs(self, sync: CatalogSync, timezone: str = "Asia/Jakarta") -> None:
        """Jobs that keep the peraturan.go.id mirror current; not tied to any schedule row."""
        for job, cron in CATALOG_JOBS.items():
            self._scheduler.add_job(
                sync.run,
                trigger=build_trigger(cron, timezone),
                id=f"{CATALOG_JOB_PREFIX}{job}",
                args=[job],
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
        LOGGER.info("Job katalog peraturan.go.id aktif: %s", ", ".join(CATALOG_JOBS))

    async def _fire(self, schedule_id: str) -> None:
        schedule = await self._repository.get_schedule(schedule_id)
        if schedule is None or not schedule.is_active:
            return
        run = await self.enqueue_schedule(schedule, trigger="CRON")
        LOGGER.info("Cron menjadwalkan run untuk %s (run_id=%s)", schedule.ministry_label, run)

    async def enqueue_schedule(self, schedule: Schedule, *, trigger: str) -> str:
        """Buat baris run lalu titipkan ke antrean; kembalikan id run."""
        run = await self._repository.create_run(
            ministry=schedule.ministry,
            ministry_label=schedule.ministry_label,
            keywords=schedule.keywords,
            trigger=trigger,
            dry_run=schedule.dry_run,
            document_limit=schedule.document_limit,
            schedule_id=schedule.id,
            source=schedule.source,
        )
        await self._repository.mark_schedule_ran(
            schedule.id, next_fire_time(schedule.cron_expression, schedule.timezone)
        )
        await self._executor.enqueue(run.id)
        return run.id


__all__ = [
    "CollectorScheduler",
    "build_trigger",
    "next_fire_time",
    "staggered_cron",
]
