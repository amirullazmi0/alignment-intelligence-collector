from __future__ import annotations

from pydantic import BaseModel, Field

# Nama field sengaja camelCase: inilah kontrak JSON yang dipakai dashboard, dan
# menyamakannya membuat payload bisa dibaca apa adanya di kedua sisi.


class ScheduleInput(BaseModel):
    """Body pembuatan jadwal cron baru.

    `ministry` kosong atau tidak dikirim berarti jadwal general: cakupannya
    seluruh JDIHN dan yang membatasi adalah `keywords`.
    """

    ministry: str | None = None
    cronExpression: str = Field(default="0 2 * * *")
    timezone: str = "Asia/Jakarta"
    keywords: list[str] = Field(default_factory=list)
    documentLimit: int = Field(default=10, ge=1, le=50)
    dryRun: bool = False
    isActive: bool = True


class ScheduleUpdate(BaseModel):
    """Body penyuntingan jadwal; hanya field terisi yang diubah.

    `ministry` sengaja tidak ada: kementerian adalah identitas jadwal, jadi memindahnya
    berarti membuat jadwal baru.
    """

    cronExpression: str | None = None
    timezone: str | None = None
    keywords: list[str] | None = None
    documentLimit: int | None = Field(default=None, ge=1, le=50)
    dryRun: bool | None = None
    isActive: bool | None = None


def paginated(
    items: list[dict[str, object]], total: int, page: int, limit: int
) -> dict[str, object]:
    """Amplop baku untuk daftar berhalaman."""
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": max(1, -(-total // limit)),
    }


__all__ = ["ScheduleInput", "ScheduleUpdate", "paginated"]
