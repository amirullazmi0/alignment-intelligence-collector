from __future__ import annotations

import asyncio
import json
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from .config import CollectorSettings
from .logging_config import configure_logging
from .models import RunSummary
from .service import CollectorService
from .state import StateStore

app = typer.Typer(no_args_is_help=True, help="Collector dokumen JDIHN untuk Alignment Intelligence")


def _settings() -> CollectorSettings:
    try:
        return CollectorSettings()
    except ValidationError as error:
        typer.echo(f"Konfigurasi tidak valid:\n{error}", err=True)
        raise typer.Exit(code=2) from error


def _print_summary(summary: RunSummary) -> None:
    payload = {
        "mode": summary.mode,
        "dry_run": summary.dry_run,
        "discovered": summary.discovered,
        "matched": summary.matched,
        "uploaded": summary.uploaded,
        "duplicates": summary.duplicates,
        "skipped_existing": summary.skipped_existing,
        "skipped_no_pdf": summary.skipped_no_pdf,
        "source_changed": summary.source_changed,
        "failed": summary.failed,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if summary.preview:
        typer.echo("\nPreview:")
        for item in summary.preview:
            typer.echo(f"- {item}")


def _run(mode: str, dry_run: bool, limit: int | None, verbose: bool) -> None:
    settings = _settings()
    configure_logging(settings.log_level, settings.log_color, verbose=verbose)
    try:
        summary = asyncio.run(CollectorService(settings).run(mode, dry_run=dry_run, limit=limit))
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
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Collect kandidat lama bertema yang belum pernah diproses."""
    _run("backfill", dry_run, limit, verbose)


@app.command()
def incremental(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Discovery tanpa login, download, atau upload")
    ] = False,
    limit: Annotated[int | None, typer.Option(min=1, help="Batas dokumen baru per run")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Collect kandidat baru dan retry kegagalan sementara."""
    _run("incremental", dry_run, limit, verbose)


@app.command("reset-failed")
def reset_failed() -> None:
    """Jadikan dokumen FAILED_PERMANENT dapat dicoba ulang pada run berikutnya."""
    settings = _settings()
    state = StateStore(settings.state_db_path)
    state.initialize()
    typer.echo(
        json.dumps({"reset": state.reset_permanent_failures()}, ensure_ascii=False, indent=2)
    )


@app.command()
def status() -> None:
    """Tampilkan ringkasan state collector lokal."""
    settings = _settings()
    state = StateStore(settings.state_db_path)
    state.initialize()
    typer.echo(
        json.dumps(
            {"documents": state.status_counts(), "last_run": state.last_run()},
            ensure_ascii=False,
            indent=2,
        )
    )
