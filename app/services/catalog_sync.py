"""Keep a full, always-current mirror of the peraturan.go.id catalogue.

Four jobs, run one at a time (one lock) so peraturan.go.id sees a single polite
client:

- ``sync_new`` (hourly): pages 1-2 of every type, where new regulations appear.
- ``sweep`` (nightly, and at start while the catalogue is empty): every list page
  from a checkpoint, so the whole catalogue is mirrored and kept "seen".
- ``refresh_details`` (hourly): detail pages, never-checked first, then the
  oldest checks. Status (Berlaku/Dicabut), relations and PDF changes are stored
  in ``regulation_changes`` and pushed to Alignment.
- ``ingest_text`` (hourly): regulations without text are registered in Alignment
  by their official PDF link; Alignment keeps only the text per Pasal/Ayat.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from ..clients.alignment import AlignmentClient, PermanentUploadError, RetryableUploadError
from ..clients.parsing import ParseError
from ..clients.pdf import PdfDownloader, PdfDownloadError
from ..clients.peraturan_go_id import (
    CATALOGUES,
    SOURCE_NAME,
    PeraturanGoIdClient,
    PeraturanGoIdRequestError,
    best_pdf,
    regulation_fingerprint,
)
from ..config import CollectorSettings
from ..domain.models import SourceDocument
from ..storage.catalog import CATALOGUE_PRIORITY, CatalogStore, ListedRegulation, Regulation
from ..storage.state import StateStore

LOGGER = logging.getLogger(__name__)

#: Checkpoint namespace of the catalogue sweep (separate from schedule runs).
CHECKPOINT_SOURCE = "PGO_CATALOG"
JOBS = ("sync_new", "sweep", "refresh_details", "ingest_text")

SOURCE_ERRORS = (httpx.TransportError, PeraturanGoIdRequestError)

PgoFactory = Callable[[], AbstractAsyncContextManager[PeraturanGoIdClient]]
AlignmentFactory = Callable[[], AbstractAsyncContextManager[AlignmentClient]]
DownloaderFactory = Callable[[], AbstractAsyncContextManager[PdfDownloader]]


@dataclass(slots=True)
class JobReport:
    job: str
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "job": self.job,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "durationSeconds": self.duration_seconds,
            "counts": dict(self.counts),
            "error": self.error,
        }


def listed_regulation(
    item_source_id: str,
    catalogue: str,
    *,
    title: str,
    document_number: str | None,
    law_year: int | None,
    institution: str | None,
    page_url: str,
    pdf_url: str | None,
) -> ListedRegulation:
    number = re.search(r"Nomor\s+(\S+)", document_number or "")
    return ListedRegulation(
        source_id=item_source_id,
        catalogue=catalogue,
        title=title,
        law_type=CATALOGUES[catalogue],
        law_number=number.group(1) if number else None,
        law_year=law_year,
        issuer=institution,
        page_url=page_url,
        pdf_url=pdf_url,
    )


def regulation_document(regulation: Regulation) -> SourceDocument:
    """The collector's document shape for a catalogue row (used by AlignmentClient)."""
    number = (
        f"Nomor {regulation.law_number} Tahun {regulation.law_year}"
        if regulation.law_number and regulation.law_year
        else None
    )
    return SourceDocument(
        source_id=regulation.source_id,
        source_url=regulation.page_url,
        title=regulation.title,
        attachment_url=regulation.pdf_url,
        category=regulation.law_type,
        document_number=number,
        law_type=regulation.law_type,
        law_number=regulation.law_number,
        law_year=regulation.law_year,
        effective_status=regulation.status,
        matched_keywords=(f"katalog {regulation.catalogue}",),
        fingerprint=regulation.fingerprint or "",
        institution=regulation.issuer,
        source_name=SOURCE_NAME,
    )


class CatalogSync:
    def __init__(
        self,
        settings: CollectorSettings,
        catalog: CatalogStore,
        state: StateStore,
        *,
        pgo_factory: PgoFactory | None = None,
        alignment_factory: AlignmentFactory | None = None,
        downloader_factory: DownloaderFactory | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._state = state
        self._pgo_factory = pgo_factory or self._default_pgo
        self._alignment_factory = alignment_factory or self._default_alignment
        self._downloader_factory = downloader_factory or self._default_downloader
        # Jobs reading peraturan.go.id's lists and pages share one lock, so the
        # site sees a single client. Sending text has its own: it must keep going
        # during the nightly sweep, which holds the first lock for over an hour.
        self._lock = asyncio.Lock()
        self._text_lock = asyncio.Lock()
        self.reports: dict[str, JobReport] = {}

    # -- factories -----------------------------------------------------------------

    def _default_pgo(self) -> AbstractAsyncContextManager[PeraturanGoIdClient]:
        return PeraturanGoIdClient(
            self._settings.peraturan_go_id_base_url,
            self._settings.http_timeout_seconds,
            self._settings.peraturan_go_id_request_delay_seconds,
        )

    def _default_alignment(self) -> AbstractAsyncContextManager[AlignmentClient]:
        email, password = self._settings.require_alignment_credentials()
        return AlignmentClient(
            self._settings.require_alignment_api_base_url(),
            email,
            password,
            self._settings.http_timeout_seconds,
        )

    def _default_downloader(self) -> AbstractAsyncContextManager[PdfDownloader]:
        return PdfDownloader(
            self._settings.http_timeout_seconds,
            self._settings.max_pdf_bytes,
            download_dir=self._settings.download_dir,
        )

    # -- running -------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def run(self, job: str) -> JobReport:
        """Run one job under the shared lock and keep its report."""
        jobs: dict[str, Callable[[JobReport], Awaitable[None]]] = {
            "sync_new": self._sync_new,
            "sweep": self._sweep,
            "refresh_details": self._refresh_details,
            "ingest_text": self._ingest_text,
        }
        if job not in jobs:
            raise ValueError(f"job tidak dikenal: {job}")
        async with self._text_lock if job == "ingest_text" else self._lock:
            report = JobReport(job=job, started_at=datetime.now(UTC).isoformat())
            self.reports[job] = report
            started = time.perf_counter()
            try:
                await jobs[job](report)
            except Exception as error:
                report.error = f"{type(error).__name__}: {error}"[:500]
                LOGGER.exception("Job katalog %s gagal", job)
            finally:
                report.finished_at = datetime.now(UTC).isoformat()
                report.duration_seconds = round(time.perf_counter() - started, 1)
                LOGGER.info("Job katalog %s selesai: %s", job, report.counts)
            return report

    async def _upsert_page(
        self, pgo: PeraturanGoIdClient, catalogue: str, page: int, report: JobReport
    ) -> tuple[int, int]:
        """Read one list page into the catalogue; return (items, new)."""
        listed = await pgo.fetch_list_page(catalogue, page)
        rows = [
            listed_regulation(
                item.source_id,
                catalogue,
                title=item.title,
                document_number=item.document_number,
                law_year=item.law_year,
                institution=item.institution,
                page_url=pgo.page_url(item.source_id),
                pdf_url=pdf,
            )
            for item, pdf in listed
        ]
        new = await self._catalog.upsert_listing(rows)
        report.counts["pages"] = report.counts.get("pages", 0) + 1
        report.counts["new"] = report.counts.get("new", 0) + new
        return len(rows), new

    async def _sync_new(self, report: JobReport) -> None:
        async with self._pgo_factory() as pgo:
            for catalogue in CATALOGUE_PRIORITY:
                try:
                    for page in (1, 2):
                        items, new = await self._upsert_page(pgo, catalogue, page, report)
                        # Page 2 only matters when page 1 was all new.
                        if items == 0 or new < items:
                            break
                except (*SOURCE_ERRORS, ParseError) as error:
                    report.counts["errors"] = report.counts.get("errors", 0) + 1
                    LOGGER.warning("Daftar %s gagal dibaca: %s", catalogue, error)

    async def _sweep(self, report: JobReport) -> None:
        budget = self._settings.pgo_sweep_max_pages
        async with self._pgo_factory() as pgo:
            for catalogue in CATALOGUE_PRIORITY:
                page = await self._state.get_checkpoint(CHECKPOINT_SOURCE, catalogue)
                try:
                    for _ in range(budget):
                        items, _new = await self._upsert_page(pgo, catalogue, page, report)
                        if items == 0:
                            # Past the last page: the next sweep starts over.
                            await self._state.set_checkpoint(CHECKPOINT_SOURCE, catalogue, 1)
                            report.counts["completed"] = report.counts.get("completed", 0) + 1
                            break
                        page += 1
                        await self._state.set_checkpoint(CHECKPOINT_SOURCE, catalogue, page)
                except (*SOURCE_ERRORS, ParseError) as error:
                    report.counts["errors"] = report.counts.get("errors", 0) + 1
                    LOGGER.warning("Sapuan %s berhenti di halaman %s: %s", catalogue, page, error)

    @asynccontextmanager
    async def _maybe_alignment(self) -> AsyncIterator[AlignmentClient | None]:
        """Alignment client, or None when its credentials are not configured."""
        try:
            manager = self._alignment_factory()
        except RuntimeError as error:
            LOGGER.warning("Alignment tidak dikonfigurasi, perubahan tidak dikirim: %s", error)
            yield None
            return
        async with manager as client:
            yield client

    async def _refresh_details(self, report: JobReport) -> None:
        due = await self._catalog.due_for_detail(self._settings.pgo_detail_per_hour)
        async with self._pgo_factory() as pgo, self._maybe_alignment() as alignment:
            for regulation in due:
                try:
                    detail = await pgo.fetch_detail_page(regulation.source_id)
                except ParseError as error:
                    # 404 and friends: the page is gone; check again next cycle.
                    await self._catalog.touch_detail(regulation.source_id)
                    report.counts["missing"] = report.counts.get("missing", 0) + 1
                    LOGGER.warning("Detail %s tidak terbaca: %s", regulation.source_id, error)
                    continue
                except SOURCE_ERRORS as error:
                    report.counts["errors"] = report.counts.get("errors", 0) + 1
                    LOGGER.warning("Detail %s gagal, berhenti: %s", regulation.source_id, error)
                    break
                pdf = best_pdf(
                    [*detail.pdf_urls, *([regulation.pdf_url] if regulation.pdf_url else [])]
                )
                relations = [{"kind": r.kind, "target": r.target} for r in detail.relations]
                changes = await self._catalog.apply_detail(
                    regulation.source_id,
                    status=detail.status,
                    issuer=detail.issuer,
                    relations=relations,
                    pdf_url=pdf,
                    fingerprint=regulation_fingerprint(
                        pdf, detail.status, regulation.title, detail.relations
                    ),
                )
                report.counts["checked"] = report.counts.get("checked", 0) + 1
                if not changes:
                    continue
                report.counts["changed"] = report.counts.get("changed", 0) + 1
                LOGGER.info("Peraturan berubah %s: %s", regulation.source_id, changes)
                metadata_changed = any(field in {"status", "relations"} for field, _, _ in changes)
                has_text = regulation.text_status in {"REGISTERED", "DUPLICATE"}
                if alignment is None or not metadata_changed or not has_text:
                    continue
                try:
                    document_id = await alignment.update_external(
                        regulation.page_url, effective_status=detail.status, relations=relations
                    )
                except (RetryableUploadError, PermanentUploadError, httpx.HTTPError) as error:
                    report.counts["pushFailed"] = report.counts.get("pushFailed", 0) + 1
                    LOGGER.warning("Perubahan %s gagal dikirim: %s", regulation.source_id, error)
                    continue
                report.counts["pushed" if document_id else "notInAlignment"] = (
                    report.counts.get("pushed" if document_id else "notInAlignment", 0) + 1
                )

    async def _ingest_text(self, report: JobReport) -> None:
        """Send regulations to Alignment, as fast as Alignment ingests them.

        With PGO_QUEUE_TARGET set, the run only fills Alignment's queue up to the
        target: an empty queue gets a full batch on the next run (every 5 minutes),
        a busy one gets nothing.
        """
        target = self._settings.pgo_queue_target
        async with self._alignment_factory() as alignment:
            if target > 0:
                waiting = await alignment.ingestion_queue()
                report.counts["queueBefore"] = waiting
                room = max(0, target - waiting)
            else:
                room = self._settings.pgo_text_per_hour
            if room == 0:
                return
            pending = await self._catalog.pending_text(room)
            if not pending:
                return
            async with self._downloader_factory() as downloader:
                for regulation in pending:
                    await self._ingest_one(regulation, alignment, downloader, report)

    async def _ingest_one(
        self,
        regulation: Regulation,
        alignment: AlignmentClient,
        downloader: PdfDownloader,
        report: JobReport,
    ) -> None:
        def count(key: str) -> None:
            report.counts[key] = report.counts.get(key, 0) + 1

        if not regulation.pdf_url:
            await self._catalog.mark_text(regulation.source_id, "NO_PDF")
            count("noPdf")
            return
        downloaded = None
        try:
            downloaded = await downloader.download(
                regulation.pdf_url, f"{regulation.source_id.removeprefix('pgo:')}.pdf"
            )
            result = await alignment.register_external(
                regulation_document(regulation),
                regulation.pdf_url,
                downloaded.sha256,
                relations=regulation.relations,
            )
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            retry = code in {403, 429} or code >= 500
            await self._catalog.mark_text(
                regulation.source_id, "RETRY" if retry else "FAILED", error=f"PDF HTTP {code}"
            )
            count("retry" if retry else "failed")
            return
        except (RetryableUploadError, httpx.TransportError) as error:
            await self._catalog.mark_text(regulation.source_id, "RETRY", error=str(error))
            count("retry")
            return
        except (PdfDownloadError, PermanentUploadError) as error:
            await self._catalog.mark_text(regulation.source_id, "FAILED", error=str(error))
            count("failed")
            return
        finally:
            if downloaded is not None:
                downloaded.path.unlink(missing_ok=True)
        await self._catalog.mark_text(
            regulation.source_id,
            "DUPLICATE" if result.duplicate else "REGISTERED",
            pdf_sha256=downloaded.sha256,
            alignment_document_id=result.document_id,
        )
        count("duplicate" if result.duplicate else "registered")


__all__ = ["CHECKPOINT_SOURCE", "JOBS", "CatalogSync", "JobReport", "regulation_document"]
