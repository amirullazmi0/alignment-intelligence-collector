from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..dependencies import get_executor

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Status service beserta kondisi antrean.

    Sengaja tanpa token: dipakai pemeriksa kesehatan container, dan tidak membocorkan
    apa pun selain panjang antrean.
    """
    executor = get_executor(request)
    return {
        "status": "ok",
        "service": "jdihn-collector",
        "queueLength": executor.queue_length,
        "currentRunId": executor.current_run_id,
    }
