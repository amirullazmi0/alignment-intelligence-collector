from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ...services.runner import stream_events, stream_run_feed
from ..dependencies import get_broker, get_repository, get_state_store
from ..middleware import Guarded
from ..schemas import paginated

router = APIRouter(prefix="/v1", tags=["runs"])

RUN_NOT_FOUND = "Run tidak ditemukan"

# Tanpa header ini, proxy yang mem-buffer akan menahan seluruh log sampai run selesai --
# justru menghapus gunanya menonton secara langsung.
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/runs")
async def list_runs(
    _: Guarded, request: Request, page: int = 1, limit: int = 20
) -> dict[str, object]:
    safe_page = max(1, page)
    safe_limit = min(max(1, limit), 200)
    items, total = await get_repository(request).list_runs(page=safe_page, limit=safe_limit)
    return paginated([item.as_dict() for item in items], total, safe_page, safe_limit)


@router.get("/runs/{run_id}")
async def get_run(_: Guarded, request: Request, run_id: str) -> dict[str, object]:
    repository = get_repository(request)
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RUN_NOT_FOUND)
    return {"run": run.as_dict(), "lines": await repository.run_logs(run_id)}


@router.get("/runs/{run_id}/events")
async def run_events(_: Guarded, request: Request, run_id: str) -> StreamingResponse:
    """Log satu run secara langsung, didahului riwayat yang sudah tersimpan."""
    repository = get_repository(request)
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RUN_NOT_FOUND)
    replay = await repository.run_logs(run_id)
    return StreamingResponse(
        stream_events(get_broker(request), run_id, replay),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@router.get("/events")
async def run_feed(_: Guarded, request: Request) -> StreamingResponse:
    """Perubahan status seluruh run, untuk halaman daftar riwayat.

    Sengaja tidak memuat baris log: daftar riwayat hanya perlu tahu ada run yang berubah,
    lalu memuat ulang datanya sendiri.
    """
    return StreamingResponse(
        stream_run_feed(get_broker(request)),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@router.get("/documents")
async def list_documents(
    _: Guarded,
    request: Request,
    status: str | None = None,
    source: str | None = None,
    runId: str | None = None,
    scheduleId: str | None = None,
    search: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, object]:
    """Documents the collector has handled: what each run and schedule produced."""
    page = max(page, 1)
    limit = min(max(limit, 1), 100)
    items, total = await get_state_store(request).list_documents(
        status=status,
        source=source,
        run_id=runId,
        schedule_id=scheduleId,
        search=search.strip() if search else None,
        page=page,
        limit=limit,
    )
    return paginated(items, total, page, limit)


@router.get("/documents/stats")
async def document_stats(_: Guarded, request: Request) -> dict[str, int]:
    """Jumlah dokumen per status pemrosesan."""
    return dict(await get_state_store(request).status_counts())
