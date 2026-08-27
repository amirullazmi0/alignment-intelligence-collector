from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from ...domain.ministries import find_ministry
from ...services.scheduler import next_fire_time
from ..dependencies import get_executor, get_repository, get_scheduler
from ..middleware import Guarded
from ..schemas import ScheduleInput, ScheduleUpdate, paginated

router = APIRouter(prefix="/v1/schedules", tags=["schedules"])

SCHEDULE_NOT_FOUND = "Jadwal tidak ditemukan"


def _validated_next_run(cron_expression: str, timezone: str) -> datetime | None:
    """Hitung jadwal berikutnya, sekaligus menolak ekspresi cron yang tidak sah.

    Validasinya dilakukan di sini, bukan dibiarkan sampai waktunya tiba, supaya jadwal
    yang salah ketik ditolak saat disimpan alih-alih diam-diam tidak pernah berjalan.
    """
    try:
        return next_fire_time(cron_expression, timezone)
    except (ValueError, KeyError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Jadwal cron tidak sah: {error}"
        ) from error


@router.get("")
async def list_schedules(
    _: Guarded,
    request: Request,
    page: int = 1,
    limit: int = 10,
    search: str = "",
) -> dict[str, object]:
    safe_page = max(1, page)
    safe_limit = min(max(1, limit), 100)
    repository = get_repository(request)
    items, total = await repository.list_schedules(page=safe_page, limit=safe_limit, search=search)
    return {
        **paginated([item.as_dict() for item in items], total, safe_page, safe_limit),
        # Seluruh kementerian yang sudah punya jadwal, bukan hanya yang tampil di halaman
        # ini, supaya dropdown pemilih tetap benar saat daftar dipaginasi.
        "takenMinistries": await repository.taken_ministries(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(_: Guarded, request: Request, body: ScheduleInput) -> dict[str, object]:
    ministry = find_ministry(body.ministry)
    if ministry is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Kementerian tidak dikenali")
    next_run_at = _validated_next_run(body.cronExpression, body.timezone)

    repository = get_repository(request)
    if await repository.get_schedule_by_ministry(ministry.value) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Jadwal untuk kementerian ini sudah ada")

    schedule = await repository.create_schedule(
        ministry=ministry.value,
        ministry_label=ministry.label,
        aliases=ministry.aliases,
        keywords=tuple(body.keywords),
        cron_expression=body.cronExpression,
        timezone=body.timezone,
        document_limit=body.documentLimit,
        dry_run=body.dryRun,
        is_active=body.isActive,
    )
    await repository.set_next_run_at(schedule.id, next_run_at)
    await get_scheduler(request).reload()
    refreshed = await repository.get_schedule(schedule.id)
    return (refreshed or schedule).as_dict()


@router.patch("/{schedule_id}")
async def update_schedule(
    _: Guarded, request: Request, schedule_id: str, body: ScheduleUpdate
) -> dict[str, object]:
    repository = get_repository(request)
    existing = await repository.get_schedule(schedule_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SCHEDULE_NOT_FOUND)

    cron_expression = body.cronExpression or existing.cron_expression
    timezone = body.timezone or existing.timezone
    changes: dict[str, object] = {"next_run_at": _validated_next_run(cron_expression, timezone)}
    if body.cronExpression is not None:
        changes["cron_expression"] = body.cronExpression
    if body.timezone is not None:
        changes["timezone"] = body.timezone
    if body.keywords is not None:
        changes["keywords"] = list(body.keywords)
    if body.documentLimit is not None:
        changes["document_limit"] = body.documentLimit
    if body.dryRun is not None:
        changes["dry_run"] = body.dryRun
    if body.isActive is not None:
        changes["is_active"] = body.isActive

    updated = await repository.update_schedule(schedule_id, changes)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SCHEDULE_NOT_FOUND)
    await get_scheduler(request).reload()
    return updated.as_dict()


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(_: Guarded, request: Request, schedule_id: str) -> None:
    if not await get_repository(request).delete_schedule(schedule_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, SCHEDULE_NOT_FOUND)
    await get_scheduler(request).reload()


@router.post("/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_schedule_now(_: Guarded, request: Request, schedule_id: str) -> dict[str, object]:
    """Jalankan satu jadwal sekarang, di luar giliran cron-nya."""
    schedule = await get_repository(request).get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SCHEDULE_NOT_FOUND)
    run_id = await get_scheduler(request).enqueue_schedule(schedule, trigger="MANUAL")
    return {"runId": run_id, "queueLength": get_executor(request).queue_length}
