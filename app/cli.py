from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from .config import CollectorSettings
from .domain.models import RunRequest, RunSummary
from .logging_config import configure_logging
from .services.collector import CollectorService
from .storage.database import Database
from .storage.repository import CollectorRepository
from .storage.state import StateStore

app = typer.Typer(no_args_is_help=True, help="Collector dokumen JDIHN untuk Alignment Intelligence")


def _settings() -> CollectorSettings:
    try:
        return CollectorSettings()
    except ValidationError as error:
        typer.echo(f"Konfigurasi tidak valid:\n{error}", err=True)
        raise typer.Exit(code=2) from error


@dataclass(frozen=True, slots=True)
class Context:
    settings: CollectorSettings
    database: Database
    state: StateStore
    repository: CollectorRepository


@asynccontextmanager
async def _context(settings: CollectorSettings) -> AsyncIterator[Context]:
    database = Database(settings.require_database_url())
    await database.connect()
    try:
        state = StateStore(database)
        await state.verify_schema()
        yield Context(settings, database, state, CollectorRepository(database))
    finally:
        await database.close()


def _print_summary(summary: RunSummary) -> None:
    typer.echo(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    if summary.preview:
        typer.echo("\nPreview:")
        for item in summary.preview:
            typer.echo(f"- {item}")


def _split_keywords(value: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in (value or "").split(",") if part.strip()))


def _build_request(
    settings: CollectorSettings,
    mode: str,
    *,
    dry_run: bool,
    limit: int | None,
    ministry: str | None,
    keywords: str | None,
) -> RunRequest:
    extra_keywords = _split_keywords(keywords)
    if ministry:
        return RunRequest.for_ministry(
            settings,
            mode,
            dry_run=dry_run,
            limit=limit,
            ministry=ministry,
            extra_keywords=extra_keywords,
        )
    request = RunRequest.from_settings(settings, mode, dry_run=dry_run, limit=limit)
    if not extra_keywords:
        return request
    return replace(
        request, keywords=_split_keywords(",".join((*request.keywords, *extra_keywords)))
    )


async def _execute(request: RunRequest) -> RunSummary:
    """Jalankan satu run lewat CLI, tetap tercatat di riwayat seperti run dari dashboard."""
    settings = _settings()
    async with _context(settings) as context:
        run = await context.repository.create_run(
            ministry=request.ministry or request.keywords[0],
            ministry_label=request.ministry or "Run manual",
            keywords=request.keywords,
            trigger="MANUAL",
            dry_run=request.dry_run,
            document_limit=request.limit,
        )
        await context.repository.start_run(run.id)
        try:
            summary = await CollectorService(settings, context.state).run(request)
        except Exception as error:
            await context.repository.fail_run(run.id, str(error) or type(error).__name__)
            raise
        await context.repository.finish_run(run.id, summary)
        return summary


def _run(
    mode: str,
    dry_run: bool,
    limit: int | None,
    verbose: bool,
    ministry: str | None = None,
    keywords: str | None = None,
) -> None:
    settings = _settings()
    configure_logging(settings.log_level, settings.log_color, verbose=verbose)
    request = _build_request(
        settings, mode, dry_run=dry_run, limit=limit, ministry=ministry, keywords=keywords
    )
    try:
        summary = asyncio.run(_execute(request))
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        message = str(error).strip() or type(error).__name__
        typer.echo(f"Collector gagal: {message}", err=True)
        raise typer.Exit(code=1) from error
    _print_summary(summary)


@app.command()
def backfill(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Discovery tanpa login, download, atau upload")
    ] = False,
    limit: Annotated[int | None, typer.Option(min=1, help="Batas dokumen baru per run")] = None,
    ministry: Annotated[
        str | None, typer.Option(help="Prioritaskan dokumen milik kementerian ini")
    ] = None,
    keywords: Annotated[str | None, typer.Option(help="Keyword tambahan, dipisah koma")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Collect kandidat lama bertema yang belum pernah diproses."""
    _run("backfill", dry_run, limit, verbose, ministry, keywords)


@app.command()
def incremental(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Discovery tanpa login, download, atau upload")
    ] = False,
    limit: Annotated[int | None, typer.Option(min=1, help="Batas dokumen baru per run")] = None,
    ministry: Annotated[
        str | None, typer.Option(help="Prioritaskan dokumen milik kementerian ini")
    ] = None,
    keywords: Annotated[str | None, typer.Option(help="Keyword tambahan, dipisah koma")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Collect kandidat baru dan retry kegagalan sementara."""
    _run("incremental", dry_run, limit, verbose, ministry, keywords)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Alamat bind, default dari .env")] = None,
    port: Annotated[int | None, typer.Option(help="Port bind, default dari .env")] = None,
    reload: Annotated[
        bool, typer.Option("--reload", help="Auto reload untuk pengembangan")
    ] = False,
) -> None:
    """Jalankan collector sebagai HTTP service beserta scheduler cron-nya."""
    import uvicorn

    from .logging_config import configure_service_logging

    settings = _settings()
    configure_service_logging(settings.log_level)
    uvicorn.run(
        "app.api:app",
        host=host or settings.collector_service_host,
        port=port or settings.collector_service_port,
        reload=reload,
    )


@app.command("reset-failed")
def reset_failed() -> None:
    """Jadikan dokumen FAILED_PERMANENT dapat dicoba ulang pada run berikutnya."""

    async def action() -> int:
        async with _context(_settings()) as context:
            return await context.state.reset_permanent_failures()

    typer.echo(json.dumps({"reset": asyncio.run(action())}, ensure_ascii=False, indent=2))


@app.command()
def status() -> None:
    """Tampilkan ringkasan state collector."""

    async def action() -> dict[str, object]:
        async with _context(_settings()) as context:
            runs, _total = await context.repository.list_runs(limit=1)
            return {
                "documents": dict(await context.state.status_counts()),
                "schedules": (await context.repository.list_schedules(limit=1))[1],
                "last_run": runs[0].as_dict() if runs else None,
            }

    typer.echo(json.dumps(asyncio.run(action()), ensure_ascii=False, indent=2))


@app.command("seed-schedules")
def seed_schedules(
    every_minutes: Annotated[
        int | None,
        typer.Option(min=1, max=1440, help="Jarak antar run dalam menit"),
    ] = None,
    every_hours: Annotated[
        int | None,
        typer.Option(min=1, max=24, help="Jarak antar run dalam jam; menimpa --every-minutes"),
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=50, help="Batas dokumen per run")] = 10,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--upload", help="Mode pratinjau atau unggah sungguhan")
    ] = False,
    replace: Annotated[bool, typer.Option("--replace", help="Timpa jadwal yang sudah ada")] = False,
) -> None:
    """Buat jadwal untuk seluruh kementerian sekaligus.

    Jadwalnya disebar merata di dalam jendela `--every-minutes`, bukan disamakan semua,
    supaya run masuk antrean bertahap alih-alih menumpuk sekaligus.
    """
    from .domain.ministries import MINISTRIES
    from .services.scheduler import next_fire_time, staggered_cron

    if every_hours is not None and every_minutes is not None:
        typer.echo("Pilih salah satu: --every-hours atau --every-minutes.", err=True)
        raise typer.Exit(code=2)
    period_minutes = every_hours * 60 if every_hours is not None else (every_minutes or 120)

    async def action() -> dict[str, object]:
        created = 0
        updated = 0
        skipped = 0
        async with _context(_settings()) as context:
            for index, ministry in enumerate(MINISTRIES):
                cron_expression = staggered_cron(index, period_minutes)
                existing = await context.repository.get_schedule_by_ministry(ministry.value)
                if existing is not None and not replace:
                    skipped += 1
                    continue
                if existing is not None:
                    await context.repository.update_schedule(
                        existing.id,
                        {
                            "cron_expression": cron_expression,
                            "document_limit": limit,
                            "dry_run": dry_run,
                            "is_active": True,
                            "next_run_at": next_fire_time(cron_expression, existing.timezone),
                        },
                    )
                    updated += 1
                    continue
                schedule = await context.repository.create_schedule(
                    ministry=ministry.value,
                    ministry_label=ministry.label,
                    aliases=ministry.aliases,
                    keywords=(),
                    cron_expression=cron_expression,
                    timezone="Asia/Jakarta",
                    document_limit=limit,
                    dry_run=dry_run,
                    is_active=True,
                )
                await context.repository.set_next_run_at(
                    schedule.id, next_fire_time(cron_expression, schedule.timezone)
                )
                created += 1
        return {
            "kementerian": len(MINISTRIES),
            "dibuat": created,
            "diperbarui": updated,
            "dilewati": skipped,
            "setiap_menit": period_minutes,
            "batas_dokumen": limit,
            "mode": "pratinjau" if dry_run else "unggah",
        }

    result = asyncio.run(action())
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    typer.echo(
        "\nJadwal tersimpan di database. Service yang sedang berjalan belum memuatnya; "
        "restart service atau panggil POST /v1/scheduler/reload.",
        err=True,
    )


@app.command("import-sqlite")
def import_sqlite(
    path: Annotated[Path, typer.Option(help="Lokasi berkas SQLite lama")] = Path(
        ".data/collector.sqlite3"
    ),
) -> None:
    """Pindahkan checkpoint dari SQLite lama ke Postgres.

    Tanpa langkah ini, dokumen yang sudah pernah dikumpulkan akan dianggap baru dan
    diunggah ulang. Baris yang sudah ada di Postgres tidak ditimpa.
    """
    import sqlite3

    if not path.exists():
        typer.echo(f"Berkas {path} tidak ditemukan; tidak ada yang diimpor.", err=True)
        raise typer.Exit(code=1)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM source_documents").fetchall()
    connection.close()

    async def action() -> dict[str, int]:
        imported = 0
        async with _context(_settings()) as context, context.database.acquire() as db:
            for row in rows:
                keywords: list[str] = json.loads(row["matched_keywords"] or "[]")
                result = await db.execute(
                    """
                    INSERT INTO source_documents (
                        source_id, source_url, title, fingerprint, attachment_url,
                        matched_keywords, status, pdf_sha256, target_document_id, attempts,
                        error_code, error_message, first_seen_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7::text::"DocumentState", $8, $9, $10,
                        $11, $12, $13::timestamptz, $14::timestamptz
                    )
                    ON CONFLICT (source_id) DO NOTHING
                    """,
                    row["source_id"],
                    row["source_url"],
                    row["title"],
                    row["fingerprint"],
                    row["attachment_url"],
                    keywords,
                    row["status"],
                    row["pdf_sha256"],
                    row["target_document_id"],
                    row["attempts"],
                    row["error_code"],
                    row["error_message"],
                    row["first_seen_at"],
                    row["updated_at"],
                )
                if result.endswith("1"):
                    imported += 1
        return {"dibaca": len(rows), "diimpor": imported, "dilewati": len(rows) - imported}

    typer.echo(json.dumps(asyncio.run(action()), ensure_ascii=False, indent=2))
