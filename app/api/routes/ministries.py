from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...domain.ministries import MINISTRIES
from ..middleware import Guarded

router = APIRouter(prefix="/v1", tags=["ministries"])


@router.get("/ministries")
async def list_ministries(_: Guarded) -> list[dict[str, Any]]:
    """Daftar kementerian yang dikurasi, beserta alias penulisannya di JDIHN."""
    return [
        {"value": item.value, "label": item.label, "aliases": list(item.aliases)}
        for item in MINISTRIES
    ]
