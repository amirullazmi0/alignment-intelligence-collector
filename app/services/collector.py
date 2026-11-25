from __future__ import annotations

import hashlib
import logging
from contextlib import AsyncExitStack
from typing import Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..clients.alignment import AlignmentClient, PermanentUploadError, RetryableUploadError
from ..clients.jdihn import JdihnClient, JdihnRequestError, ParseError
from ..clients.pasal import SOURCE_ID_PREFIX, PasalClient, PasalRequestError
from ..clients.pdf import PdfDownloader, PdfDownloadError, UnresolvedPdfHostError
from ..clients.peraturan_go_id import SOURCE_ID_PREFIX as PGO_PREFIX
from ..clients.peraturan_go_id import PeraturanGoIdClient, PeraturanGoIdRequestError
from ..config import CollectorSettings
from ..domain.models import (
    CollectorSource,
    DocumentState,
    DownloadedPdf,
    RunRequest,
    RunSummary,
    SearchItem,
    SourceDocument,
    UploadResult,
)
from ..storage.state import StateStore
from .ministry import matches_ministry

LOGGER = logging.getLogger(__name__)


def _error_text(error: BaseException) -> str:
    """Pesan error yang selalu informatif.

    httpx membungkus kegagalan TLS dan koneksi dalam exception yang `str()`-nya kerap
    kosong, sehingga error_message di SQLite ikut kosong dan kegagalan menjadi tidak bisa
    didiagnosis sama sekali. Penyebab aslinya sering berada dua lapis lebih dalam
    (httpx -> httpcore -> OSError), jadi rantai `__cause__` ditelusuri sampai ketemu
    pesan yang benar-benar berisi.
    """
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = " ".join(str(current).split())
        if message:
            return message if current is error else f"{type(error).__name__}: {message}"
        current = current.__cause__ or current.__context__
    return type(error).__name__


class DiscoveryClient(Protocol):
    """Antarmuka bersama JdihnClient dan PasalClient."""

    async def discover(
        self,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        *,
        ministry: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> list[SearchItem]: ...

    async def fetch_detail(
        self,
        item: SearchItem,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
    ) -> SourceDocument: ...


#: Kegagalan jaringan/sementara dari sumber mana pun; kandidatnya dicoba lagi run berikutnya.
SOURCE_NETWORK_ERRORS = (
    httpx.TransportError,
    JdihnRequestError,
    PasalRequestError,
    PeraturanGoIdRequestError,
)


def _source_url(item: SearchItem, base_url: str) -> str:
    if item.source_id.startswith(PGO_PREFIX):
        return f"https://peraturan.go.id/id/{item.source_id[len(PGO_PREFIX) :]}"
    if item.source_id.startswith(SOURCE_ID_PREFIX):
        return f"https://pasal.id/api/v1/laws/{item.source_id[len(SOURCE_ID_PREFIX) :]}"
    return f"{base_url.rstrip('/')}/doc/{item.source_id}"


def _pdf_filename(document: SourceDocument) -> str:
    if document.source_id.startswith(PGO_PREFIX):
        return f"pgo-{document.source_id[len(PGO_PREFIX) :]}.pdf"
    if document.source_id.startswith(SOURCE_ID_PREFIX):
        slug = document.source_id[len(SOURCE_ID_PREFIX) :].replace("/", "-")
        return f"pasal-{slug}.pdf"
    return f"jdihn-{document.source_id}.pdf"


def _stub_document(item: SearchItem, base_url: str) -> SourceDocument:
    fingerprint = hashlib.sha256(item.searchable_text.encode()).hexdigest()
    return SourceDocument(
        source_id=item.source_id,
        source_url=_source_url(item, base_url),
        title=item.title,
        attachment_url=None,
        category=item.category,
        document_number=item.document_number,
        law_type=item.law_type,
        law_number=None,
        law_year=item.law_year,
        effective_status=item.effective_status,
        matched_keywords=(),
        fingerprint=fingerprint,
    )


class CollectorService:
    def __init__(self, settings: CollectorSettings, state: StateStore) -> None:
        self._settings = settings
        self._state = state

    def _source_client(
        self, source: CollectorSource, mode: str = "backfill"
    ) -> JdihnClient | PasalClient | PeraturanGoIdClient:
        if source == CollectorSource.PERATURAN_GO_ID:
            name = CollectorSource.PERATURAN_GO_ID.value

            async def get_checkpoint(catalogue: str) -> int:
                return await self._state.get_checkpoint(name, catalogue)

            async def set_checkpoint(catalogue: str, page: int) -> None:
                await self._state.set_checkpoint(name, catalogue, page)

            return PeraturanGoIdClient(
                self._settings.peraturan_go_id_base_url,
                self._settings.http_timeout_seconds,
                self._settings.peraturan_go_id_request_delay_seconds,
                mode=mode,
                is_known=self._state.is_known,
                get_checkpoint=get_checkpoint,
                set_checkpoint=set_checkpoint,
                max_pages_per_run=self._settings.peraturan_go_id_max_pages,
            )
        if source == CollectorSource.PASAL_ID:
            return PasalClient(
                self._settings.pasal_api_base_url,
                self._settings.require_pasal_token(),
                self._settings.http_timeout_seconds,
                self._settings.pasal_request_delay_seconds,
            )
        return JdihnClient(
            self._settings.jdihn_base_url,
            self._settings.http_timeout_seconds,
            self._settings.request_delay_seconds,
        )

    async def run(self, request: RunRequest) -> RunSummary:
        """Jalankan satu run dan kembalikan ringkasannya.

        Pencatatan baris `collector_runs` sengaja bukan tanggung jawab service ini:
        pemanggilnya -- scheduler, API, atau CLI -- yang membuat baris run lebih dulu lalu
        menyimpan ringkasan hasilnya. Dengan begitu run yang gagal sebelum service ini
        sempat berjalan pun tetap punya jejak.
        """
        dry_run = request.dry_run
        summary = RunSummary(
            mode=request.mode,
            dry_run=dry_run,
            ministry=request.ministry,
            source=request.source.value,
        )
        max_actions = request.limit or self._settings.max_uploads_per_run
        source_label, error_prefix = {
            CollectorSource.PASAL_ID: ("pasal.id", "PASAL"),
            CollectorSource.PERATURAN_GO_ID: ("peraturan.go.id", "PGO"),
        }.get(request.source, ("JDIHN", "JDIHN"))

        try:
            async with AsyncExitStack() as stack:
                source: DiscoveryClient = await stack.enter_async_context(
                    self._source_client(request.source, request.mode)
                )
                alignment: AlignmentClient | None = None
                downloader: PdfDownloader | None = None
                if not dry_run:
                    email, password = self._settings.require_alignment_credentials()
                    alignment = await stack.enter_async_context(
                        AlignmentClient(
                            self._settings.require_alignment_api_base_url(),
                            email,
                            password,
                            self._settings.http_timeout_seconds,
                        )
                    )
                    downloader = await stack.enter_async_context(
                        PdfDownloader(
                            self._settings.http_timeout_seconds,
                            self._settings.max_pdf_bytes,
                            download_dir=self._settings.download_dir,
                        )
                    )

                candidates = await source.discover(
                    request.keywords,
                    request.exclude_keywords,
                    ministry=request.ministry,
                    aliases=request.ministry_aliases,
                )
                summary.discovered = len(candidates)
                if request.ministry is not None:
                    summary.ministry_matched = sum(
                        1
                        for candidate in candidates
                        if matches_ministry(candidate, request.ministry, request.ministry_aliases)
                    )
                actions = 0
                for item in candidates:
                    if actions >= max_actions:
                        break
                    try:
                        document = await source.fetch_detail(
                            item, request.keywords, request.exclude_keywords
                        )
                    except SOURCE_NETWORK_ERRORS as error:
                        message = _error_text(error)
                        summary.failed += 1
                        if not dry_run:
                            await self._state.record_fetch_failure(
                                _stub_document(item, self._settings.jdihn_base_url),
                                DocumentState.FAILED_RETRYABLE,
                                error_code=f"{error_prefix}_NETWORK_ERROR",
                                error_message=message,
                            )
                        LOGGER.warning(
                            "Gagal mengambil detail %s source_id=%s, "
                            "lanjut kandidat berikutnya: %s",
                            source_label,
                            item.source_id,
                            message,
                        )
                        continue
                    except ParseError as error:
                        message = _error_text(error)
                        summary.failed += 1
                        if not dry_run:
                            await self._state.record_fetch_failure(
                                _stub_document(item, self._settings.jdihn_base_url),
                                DocumentState.PARSE_FAILED,
                                error_code=f"{error_prefix}_PARSE_ERROR",
                                error_message=message,
                            )
                        LOGGER.error(
                            "Gagal parsing %s source_id=%s: %s",
                            source_label,
                            item.source_id,
                            message,
                        )
                        continue

                    if not document.matched_keywords:
                        continue
                    summary.matched += 1
                    existing = await self._state.get(document.source_id)
                    if existing and existing.fingerprint == document.fingerprint:
                        if existing.status != DocumentState.FAILED_RETRYABLE:
                            summary.skipped_existing += 1
                            continue
                    elif existing and existing.status in {
                        DocumentState.UPLOADED,
                        DocumentState.DUPLICATE,
                        DocumentState.SOURCE_CHANGED,
                    }:
                        summary.source_changed += 1
                        if not dry_run:
                            await self._state.record(document, DocumentState.SOURCE_CHANGED)
                        LOGGER.warning(
                            "Source berubah dan perlu review source_id=%s", document.source_id
                        )
                        continue

                    if not document.attachment_url:
                        # Tidak menghabiskan jatah: batasnya menghitung dokumen yang
                        # benar-benar diunduh dan diunggah, dan dokumen tanpa lampiran
                        # tidak menghasilkan apa pun.
                        summary.skipped_no_pdf += 1
                        if not dry_run:
                            await self._state.record(document, DocumentState.SKIPPED_NO_PDF)
                        continue

                    actions += 1
                    if dry_run:
                        summary.preview.append(
                            f"{document.source_id} | {document.title} | {document.attachment_url}"
                        )
                        continue

                    if alignment is None or downloader is None:
                        raise AssertionError("Client upload belum diinisialisasi")
                    await self._process_document(document, downloader, alignment, summary)
        except Exception:
            summary.failed += 1
            raise
        return summary

    async def _process_document(
        self,
        document: SourceDocument,
        downloader: PdfDownloader,
        alignment: AlignmentClient,
        summary: RunSummary,
    ) -> None:
        if document.attachment_url is None:
            raise AssertionError("Document tidak memiliki attachment URL")
        downloaded = None
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type(httpx.TransportError),
                reraise=True,
            ):
                with attempt:
                    downloaded = await downloader.download(
                        document.attachment_url, _pdf_filename(document)
                    )

            if downloaded is None:
                raise AssertionError("Download selesai tanpa file")
            result: UploadResult | None = None
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type(RetryableUploadError),
                reraise=True,
            ):
                with attempt:
                    result = await alignment.upload(document, downloaded.path, downloaded.filename)
            if result is None:
                raise AssertionError("Upload selesai tanpa result")
            if result.duplicate:
                summary.duplicates += 1
                await self._state.record(
                    document,
                    DocumentState.DUPLICATE,
                    pdf_sha256=downloaded.sha256,
                    increment_attempts=True,
                )
            else:
                summary.uploaded += 1
                await self._state.record(
                    document,
                    DocumentState.UPLOADED,
                    pdf_sha256=downloaded.sha256,
                    target_document_id=result.document_id,
                    increment_attempts=True,
                )
                LOGGER.info(
                    "Upload queued source_id=%s document_id=%s",
                    document.source_id,
                    result.document_id,
                )
        except RetryableUploadError as error:
            await self._fail(
                document,
                summary,
                DocumentState.FAILED_RETRYABLE,
                "ALIGNMENT_RETRYABLE",
                error,
                downloaded,
            )
        except httpx.HTTPStatusError as error:
            # 5xx dan 429 dari server sumber bersifat sementara. Menandainya permanen
            # membuat dokumen tidak pernah dicoba lagi, karena FAILED_PERMANENT tidak
            # ikut di-retry pada run berikutnya.
            status_code = error.response.status_code
            # 403 is often a WAF or rate rule on JDIH sites, not a missing file; it is
            # retried like 5xx, and the attempt cap in the state store ends it.
            retryable = status_code in {403, 429} or status_code >= 500
            await self._fail(
                document,
                summary,
                DocumentState.FAILED_RETRYABLE if retryable else DocumentState.FAILED_PERMANENT,
                f"PDF_HTTP_{status_code}",
                error,
                downloaded,
            )
        except (PdfDownloadError, PermanentUploadError) as error:
            await self._fail(
                document,
                summary,
                DocumentState.FAILED_PERMANENT,
                (
                    "PDF_HOST_UNRESOLVED"
                    if isinstance(error, UnresolvedPdfHostError)
                    else type(error).__name__
                ),
                error,
                downloaded,
            )
        except httpx.TransportError as error:
            await self._fail(
                document,
                summary,
                DocumentState.FAILED_RETRYABLE,
                "ALIGNMENT_NETWORK_ERROR" if downloaded else "PDF_NETWORK_ERROR",
                error,
                downloaded,
            )

    async def _fail(
        self,
        document: SourceDocument,
        summary: RunSummary,
        status: DocumentState,
        error_code: str,
        error: BaseException,
        downloaded: DownloadedPdf | None,
    ) -> None:
        """Catat kegagalan ke state sekaligus ke log.

        Sebelumnya cabang except hanya menulis ke SQLite, sehingga satu-satunya jalur yang
        benar-benar mengunduh dan mengunggah justru tidak meninggalkan jejak di log dan
        ringkasan run melaporkan kegagalan tanpa satu pun keterangan penyebabnya.
        """
        message = _error_text(error)
        summary.failed += 1
        await self._state.record(
            document,
            status,
            pdf_sha256=downloaded.sha256 if downloaded else None,
            error_code=error_code,
            error_message=message,
            increment_attempts=True,
        )
        LOGGER.error(
            "Gagal memproses source_id=%s status=%s code=%s: %s",
            document.source_id,
            status.value,
            error_code,
            message,
        )
