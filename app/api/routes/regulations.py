"""Read the peraturan.go.id mirror and trigger its sync jobs."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from ...services.catalog_sync import JOBS
from ..dependencies import get_catalog, get_catalog_sync
from ..middleware import Guarded
from ..schemas import paginated

router = APIRouter(prefix="/v1/regulations", tags=["regulations"])


def _item(regulation: object) -> dict[str, object]:
    data = asdict(regulation)  # type: ignore[call-overload]
    return {
        "sourceId": data["source_id"],
        "catalogue": data["catalogue"],
        "title": data["title"],
        "lawType": data["law_type"],
        "lawNumber": data["law_number"],
        "lawYear": data["law_year"],
        "issuer": data["issuer"],
        "status": data["status"],
        "relations": data["relations"],
        "pageUrl": data["page_url"],
        "pdfUrl": data["pdf_url"],
        "textStatus": data["text_status"],
        "alignmentDocumentId": data["alignment_document_id"],
    }


@router.get("")
async def list_regulations(
    _: Guarded,
    request: Request,
    catalogue: str | None = None,
    year: int | None = None,
    status_: str | None = Query(default=None, alias="status"),
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    items, total = await get_catalog(request).search(
        catalogue=catalogue, year=year, status=status_, query=q, page=page, limit=limit
    )
    return paginated([_item(item) for item in items], total, page, limit)


@router.get("/stats")
async def regulation_stats(_: Guarded, request: Request) -> dict[str, object]:
    sync = get_catalog_sync(request)
    stats = await get_catalog(request).stats()
    return {
        **stats,
        "busy": sync.busy,
        "jobs": {job: report.as_dict() for job, report in sync.reports.items()},
    }


@router.get("/{source_id}")
async def get_regulation(_: Guarded, request: Request, source_id: str) -> dict[str, object]:
    regulation = await get_catalog(request).get(source_id)
    if regulation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Peraturan tidak ditemukan")
    return _item(regulation)


@router.post("/sync/{job}", status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(_: Guarded, request: Request, job: str) -> dict[str, object]:
    """Start a sync job now, in the background (it waits for a running one)."""
    if job not in JOBS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Job harus salah satu dari {', '.join(JOBS)}"
        )
    sync = get_catalog_sync(request)
    tasks: set[asyncio.Task[Any]] = request.app.state.background_tasks
    task = asyncio.create_task(sync.run(job))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"job": job, "queued": True, "busy": sync.busy}
