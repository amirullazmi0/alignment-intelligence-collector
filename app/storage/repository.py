from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..domain.models import RunSummary
from .database import Database

MAX_ERROR_MESSAGE = 2000


def _affected(command_tag: str) -> int:
    """Jumlah baris dari command tag asyncpg, contohnya "UPDATE 12"."""
    _, _, count = command_tag.rpartition(" ")
    return int(count) if count.isdigit() else 0


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Schedule:
    id: str
    #: None berarti jadwal general yang mencakup seluruh JDIHN.
    ministry: str | None
    ministry_label: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    cron_expression: str
    timezone: str
    document_limit: int
    dry_run: bool
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "ministry": self.ministry,
            "ministryLabel": self.ministry_label,
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
            "cronExpression": self.cron_expression,
            "timezone": self.timezone,
            "documentLimit": self.document_limit,
            "dryRun": self.dry_run,
            "isActive": self.is_active,
            "lastRunAt": self.last_run_at.isoformat() if self.last_run_at else None,
            "nextRunAt": self.next_run_at.isoformat() if self.next_run_at else None,
        }


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    schedule_id: str | None
    #: None berarti run general yang tidak menyasar satu kementerian tertentu.
    ministry: str | None
    ministry_label: str
    keywords: tuple[str, ...]
    trigger: str
    status: str
    dry_run: bool
    document_limit: int | None
    started_at: datetime | None
    finished_at: datetime | None
    summary: dict[str, Any] | None
    error_message: str | None
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "scheduleId": self.schedule_id,
            "ministry": self.ministry,
            "ministryLabel": self.ministry_label,
            "keywords": list(self.keywords),
            "trigger": self.trigger,
            "status": self.status,
            "dryRun": self.dry_run,
            "documentLimit": self.document_limit,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "summary": self.summary,
            "errorMessage": self.error_message,
            "createdAt": self.created_at.isoformat(),
        }


SCHEDULE_COLUMNS = """
    id::text, ministry, ministry_label, aliases, keywords, cron_expression, timezone,
    document_limit, dry_run, is_active, last_run_at, next_run_at
"""

RUN_COLUMNS = """
    id::text, schedule_id::text, ministry, ministry_label, keywords, trigger::text,
    status::text, dry_run, document_limit, started_at, finished_at, summary,
    error_message, created_at
"""


def _schedule(row: Any) -> Schedule:
    return Schedule(
        id=row["id"],
        ministry=row["ministry"],
        ministry_label=row["ministry_label"],
        aliases=tuple(row["aliases"] or ()),
        keywords=tuple(row["keywords"] or ()),
        cron_expression=row["cron_expression"],
        timezone=row["timezone"],
        document_limit=row["document_limit"],
        dry_run=row["dry_run"],
        is_active=row["is_active"],
        last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"],
    )


def _run(row: Any) -> Run:
    raw_summary = row["summary"]
    summary: dict[str, Any] | None = None
    if isinstance(raw_summary, str):
        summary = json.loads(raw_summary)
    elif isinstance(raw_summary, dict):
        summary = raw_summary
    return Run(
        id=row["id"],
        schedule_id=row["schedule_id"],
        ministry=row["ministry"],
        ministry_label=row["ministry_label"],
        keywords=tuple(row["keywords"] or ()),
        trigger=row["trigger"],
        status=row["status"],
        dry_run=row["dry_run"],
        document_limit=row["document_limit"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        summary=summary,
        error_message=row["error_message"],
        created_at=row["created_at"],
    )


class CollectorRepository:
    """Query jadwal cron, riwayat run, dan log.

    Schema-nya dimiliki Prisma; di sini semuanya SQL biasa lewat asyncpg. Kolom
    `updated_at` diisi eksplisit karena `@updatedAt` milik Prisma berjalan di sisi client,
    bukan sebagai trigger database.
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    # ---------------------------------------------------------------- schedules

    async def list_schedules(
        self, *, page: int = 1, limit: int = 10, search: str = ""
    ) -> tuple[list[Schedule], int]:
        """Satu halaman jadwal beserta jumlah totalnya.

        Pencarian mencakup nama kementerian sekaligus keyword tambahannya, sehingga
        jadwal bisa ditemukan lewat tema yang dikumpulkannya, bukan hanya namanya.

        Urutannya jadwal terbaru lebih dulu. Sejak satu kementerian boleh punya beberapa
        jadwal, urutan menurut nama kementerian membuat jadwal yang baru dibuat terselip
        di tengah daftar dan sulit dipastikan benar-benar tersimpan.
        """
        needle = search.strip()
        where = ""
        params: list[object] = []
        if needle:
            params.append(f"%{needle}%")
            where = """
                WHERE ministry_label ILIKE $1
                   OR ministry ILIKE $1
                   OR array_to_string(keywords, ' ') ILIKE $1
            """

        offset = max(0, (page - 1) * limit)
        async with self._db.acquire() as connection:
            total = await connection.fetchval(
                f"SELECT COUNT(*) FROM ministry_schedules {where}", *params
            )
            rows = await connection.fetch(
                f"""
                SELECT {SCHEDULE_COLUMNS} FROM ministry_schedules {where}
                ORDER BY created_at DESC, id
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                offset,
            )
        return [_schedule(row) for row in rows], int(total or 0)

    async def get_schedule_by_ministry(self, ministry: str) -> Schedule | None:
        """Jadwal pertama milik sebuah kementerian.

        Sejak satu kementerian boleh punya beberapa jadwal, ini tidak lagi dipakai untuk
        menolak duplikat -- hanya untuk seeding, yang perlu tahu apakah kementerian itu
        sudah pernah dijadwalkan sama sekali.
        """
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT {SCHEDULE_COLUMNS} FROM ministry_schedules WHERE ministry = $1",
                ministry,
            )
        return _schedule(row) if row else None

    async def active_schedules(self) -> list[Schedule]:
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {SCHEDULE_COLUMNS} FROM ministry_schedules
                WHERE is_active ORDER BY ministry_label
                """
            )
        return [_schedule(row) for row in rows]

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT {SCHEDULE_COLUMNS} FROM ministry_schedules WHERE id = $1::uuid",
                schedule_id,
            )
        return _schedule(row) if row else None

    async def create_schedule(
        self,
        *,
        ministry: str | None,
        ministry_label: str,
        aliases: tuple[str, ...],
        keywords: tuple[str, ...],
        cron_expression: str,
        timezone: str,
        document_limit: int,
        dry_run: bool,
        is_active: bool,
    ) -> Schedule:
        timestamp = _now()
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                INSERT INTO ministry_schedules (
                    id, ministry, ministry_label, aliases, keywords, cron_expression,
                    timezone, document_limit, dry_run, is_active, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10
                )
                RETURNING {SCHEDULE_COLUMNS}
                """,
                ministry,
                ministry_label,
                list(aliases),
                list(keywords),
                cron_expression,
                timezone,
                document_limit,
                dry_run,
                is_active,
                timestamp,
            )
        return _schedule(row)

    async def update_schedule(
        self, schedule_id: str, changes: dict[str, object]
    ) -> Schedule | None:
        """Perbarui sebagian kolom. `changes` memakai nama kolom database."""
        if not changes:
            return await self.get_schedule(schedule_id)
        columns = list(changes)
        assignments = ", ".join(f"{name} = ${index + 2}" for index, name in enumerate(columns))
        values = [changes[name] for name in columns]
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE ministry_schedules
                SET {assignments}, updated_at = ${len(columns) + 2}
                WHERE id = $1::uuid
                RETURNING {SCHEDULE_COLUMNS}
                """,
                schedule_id,
                *values,
                _now(),
            )
        return _schedule(row) if row else None

    async def delete_schedule(self, schedule_id: str) -> bool:
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                "DELETE FROM ministry_schedules WHERE id = $1::uuid RETURNING id",
                schedule_id,
            )
        return row is not None

    async def mark_schedule_ran(self, schedule_id: str, next_run_at: datetime | None) -> None:
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                UPDATE ministry_schedules
                SET last_run_at = $2, next_run_at = $3, updated_at = $2
                WHERE id = $1::uuid
                """,
                schedule_id,
                _now(),
                next_run_at,
            )

    async def set_next_run_at(self, schedule_id: str, next_run_at: datetime | None) -> None:
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                UPDATE ministry_schedules
                SET next_run_at = $2, updated_at = $3
                WHERE id = $1::uuid
                """,
                schedule_id,
                next_run_at,
                _now(),
            )

    # --------------------------------------------------------------------- runs

    async def create_run(
        self,
        *,
        ministry: str | None,
        ministry_label: str,
        keywords: tuple[str, ...],
        trigger: str,
        dry_run: bool,
        document_limit: int | None,
        schedule_id: str | None = None,
    ) -> Run:
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                INSERT INTO collector_runs (
                    id, schedule_id, ministry, ministry_label, keywords, trigger, status,
                    dry_run, document_limit, created_at
                ) VALUES (
                    gen_random_uuid(), $1::uuid, $2, $3, $4, $5::text::"RunTrigger",
                    'QUEUED'::"RunStatus", $6, $7, $8
                )
                RETURNING {RUN_COLUMNS}
                """,
                schedule_id,
                ministry,
                ministry_label,
                list(keywords),
                trigger,
                dry_run,
                document_limit,
                _now(),
            )
        return _run(row)

    async def start_run(self, run_id: str) -> None:
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                UPDATE collector_runs
                SET status = 'RUNNING'::"RunStatus", started_at = $2
                WHERE id = $1::uuid
                """,
                run_id,
                _now(),
            )

    async def finish_run(self, run_id: str, summary: RunSummary) -> None:
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                UPDATE collector_runs
                SET status = 'SUCCEEDED'::"RunStatus", finished_at = $2, summary = $3::jsonb
                WHERE id = $1::uuid
                """,
                run_id,
                _now(),
                json.dumps(summary.as_dict(), ensure_ascii=False),
            )

    async def fail_run(self, run_id: str, message: str) -> None:
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                UPDATE collector_runs
                SET status = 'FAILED'::"RunStatus", finished_at = $2, error_message = $3
                WHERE id = $1::uuid
                """,
                run_id,
                _now(),
                message[:MAX_ERROR_MESSAGE],
            )

    async def recover_interrupted_runs(self, message: str) -> int:
        """Tandai gagal seluruh run yang masih RUNNING dari proses sebelumnya.

        Antrean eksekutor hanya hidup di memori, jadi sebuah run yang terputus oleh
        restart tidak akan pernah dilanjutkan siapa pun. Tanpa rekonsiliasi ini
        barisnya menggantung RUNNING selamanya dan dashboard menghitungnya sebagai
        pekerjaan aktif yang sebenarnya sudah mati.

        Dipanggil sekali saat startup, jadi ia mengasumsikan hanya ada satu instance
        collector. Menjalankan dua instance sekaligus akan membuat yang satu
        menggagalkan run milik yang lain.
        """
        async with self._db.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE collector_runs
                SET status = 'FAILED'::"RunStatus", finished_at = $1, error_message = $2
                WHERE status = 'RUNNING'::"RunStatus"
                """,
                _now(),
                message[:MAX_ERROR_MESSAGE],
            )
        return _affected(result)

    async def pending_run_ids(self) -> list[str]:
        """Run yang masih QUEUED, terlama lebih dulu.

        Dipakai saat startup untuk memuat ulang antrean memori. Tanpa ini setiap
        restart meninggalkan baris QUEUED yang tidak akan pernah diambil lagi.
        """
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id FROM collector_runs
                WHERE status = 'QUEUED'::"RunStatus"
                ORDER BY created_at
                """
            )
        return [str(row["id"]) for row in rows]

    async def get_run(self, run_id: str) -> Run | None:
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT {RUN_COLUMNS} FROM collector_runs WHERE id = $1::uuid", run_id
            )
        return _run(row) if row else None

    async def list_runs(self, *, page: int = 1, limit: int = 20) -> tuple[list[Run], int]:
        """Satu halaman riwayat run beserta jumlah totalnya, terbaru lebih dulu."""
        offset = max(0, (page - 1) * limit)
        async with self._db.acquire() as connection:
            total = await connection.fetchval("SELECT COUNT(*) FROM collector_runs")
            rows = await connection.fetch(
                f"""
                SELECT {RUN_COLUMNS} FROM collector_runs
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [_run(row) for row in rows], int(total or 0)

    # ----------------------------------------------------------------- run logs

    async def append_logs(self, run_id: str, lines: list[dict[str, Any]]) -> None:
        if not lines:
            return
        async with self._db.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO collector_run_logs (run_id, ts, level, logger, message)
                VALUES ($1::uuid, $2, $3, $4, $5)
                """,
                [
                    (
                        run_id,
                        line["ts"],
                        str(line.get("level", "INFO")),
                        str(line.get("logger", "")),
                        str(line.get("message", "")),
                    )
                    for line in lines
                ],
            )

    async def run_logs(self, run_id: str) -> list[dict[str, object]]:
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT ts, level, logger, message
                FROM collector_run_logs WHERE run_id = $1::uuid ORDER BY id
                """,
                run_id,
            )
        return [
            {
                "type": "log",
                "ts": row["ts"].isoformat(),
                "level": row["level"],
                "logger": row["logger"],
                "message": row["message"],
            }
            for row in rows
        ]


__all__ = ["CollectorRepository", "Run", "Schedule"]
