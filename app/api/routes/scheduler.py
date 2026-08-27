from __future__ import annotations

from fastapi import APIRouter, Request

from ..dependencies import get_repository, get_scheduler
from ..middleware import Guarded

router = APIRouter(prefix="/v1/scheduler", tags=["scheduler"])


@router.post("/reload")
async def reload_scheduler(_: Guarded, request: Request) -> dict[str, object]:
    """Muat ulang jadwal dari database.

    Dibutuhkan setelah jadwal diubah dari luar service, misalnya lewat `yarn seed`:
    scheduler menyimpan job-nya di memori, jadi ia tidak tahu perubahan yang ditulis
    langsung ke database.
    """
    await get_scheduler(request).reload()
    _items, total = await get_repository(request).list_schedules(limit=1)
    return {"reloaded": True, "schedules": total}
