from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from app.clients.alignment import AlignmentClient, PermanentUploadError
from app.clients.pdf import PdfDownloader
from app.config import CollectorSettings
from app.domain.models import DocumentState, RunSummary, SourceDocument
from app.services.collector import CollectorService, _error_text
from app.storage.state import StateStore, StoredDocument


def _document() -> SourceDocument:
    return SourceDocument(
        source_id="42",
        source_url="https://jdihn.go.id/doc/42",
        title="Peraturan Usaha",
        attachment_url="https://files.example.org/42.pdf",
        category="peraturan",
        document_number="Nomor 7 Tahun 2024",
        law_type="PERATURAN KEMENTERIAN",
        law_number="7",
        law_year=2024,
        effective_status="Berlaku",
        matched_keywords=("usaha",),
        fingerprint="fingerprint",
    )


def _settings() -> CollectorSettings:
    return CollectorSettings(
        _env_file=None,
        DATABASE_URL="postgresql://localhost/tidak-dipakai",
    )


class RecordingStateStore(StateStore):
    """StateStore tiruan yang menyimpan di memori.

    Test ini menguji klasifikasi kegagalan dan logging milik CollectorService, bukan SQL
    di lapisan penyimpanan, jadi tidak perlu database sungguhan.
    """

    def __init__(self) -> None:  # tidak memanggil super(): tidak ada koneksi yang dipakai
        self.records: list[tuple[str, DocumentState, str | None]] = []

    async def verify_schema(self) -> None:
        return None

    async def get(self, source_id: str) -> StoredDocument | None:
        return None

    async def record(
        self,
        document: SourceDocument,
        status: DocumentState,
        *,
        pdf_sha256: str | None = None,
        target_document_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        self.records.append((document.source_id, status, error_code))

    async def record_fetch_failure(
        self,
        document: SourceDocument,
        status: DocumentState,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self.records.append((document.source_id, status, error_code))

    @property
    def last_status(self) -> DocumentState:
        return self.records[-1][1]


def test_error_text_falls_back_when_str_is_empty() -> None:
    """Kegagalan TLS dari httpx kerap punya str() kosong."""
    assert _error_text(httpx.ConnectError("")) == "ConnectError"

    with_cause = httpx.ConnectError("")
    with_cause.__cause__ = OSError("handshake failure")
    assert _error_text(with_cause) == "ConnectError: handshake failure"

    assert _error_text(ValueError("pesan asli")) == "pesan asli"


@pytest.mark.asyncio
async def test_source_server_error_is_retryable_and_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP 500 dari JDIH sumber bersifat sementara, bukan kegagalan permanen."""

    async def allow_test_host(_url: str) -> None:
        return None

    monkeypatch.setattr("app.clients.pdf._validate_public_https_url", allow_test_host)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    settings = _settings()
    state = RecordingStateStore()
    service = CollectorService(settings, state)
    summary = RunSummary(mode="backfill", dry_run=False)
    document = _document()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        downloader = PdfDownloader(5, 1024, http_client, tmp_path)
        with caplog.at_level(logging.ERROR, logger="app.services.collector"):
            await service._process_document(
                document,
                downloader,
                AlignmentClient("https://alignment.example", "a@b.test", "x", 5, http_client),
                summary,
            )

    assert state.last_status == DocumentState.FAILED_RETRYABLE
    assert summary.failed == 1
    assert "PDF_HTTP_500" in caplog.text
    assert "source_id=42" in caplog.text


@pytest.mark.asyncio
async def test_download_failure_is_written_to_the_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Kegagalan permanen pun harus meninggalkan jejak, bukan hanya baris di SQLite."""
    settings = _settings()
    state = RecordingStateStore()
    service = CollectorService(settings, state)
    summary = RunSummary(mode="backfill", dry_run=False)

    async with httpx.AsyncClient() as http_client:
        downloader = PdfDownloader(5, 1024, http_client, tmp_path)
        with caplog.at_level(logging.ERROR, logger="app.services.collector"):
            await service._process_document(
                _document(),
                downloader,
                AlignmentClient("https://alignment.example", "a@b.test", "x", 5, http_client),
                summary,
            )

    assert state.last_status == DocumentState.FAILED_PERMANENT
    assert "UnsafePdfUrlError" in caplog.text


@pytest.mark.asyncio
async def test_alignment_does_not_swallow_unrelated_bad_request(tmp_path: Path) -> None:
    """400 yang bukan soal lawYear tidak boleh disamarkan jadi retry tanpa lawYear."""
    upload_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_count
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        upload_count += 1
        return httpx.Response(400, json={"message": "documentKind tidak dikenal"})

    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AlignmentClient(
            "https://alignment.example", "bot@example.test", "secret", 5, http_client
        )
        with pytest.raises(PermanentUploadError, match="documentKind"):
            await client.upload(_document(), pdf, "document.pdf")

    assert upload_count == 1


def test_error_text_walks_the_whole_cause_chain() -> None:
    """httpx -> httpcore -> OSError: pesan asli sering dua lapis di bawah."""
    root = ConnectionResetError("[WinError 10054] koneksi ditutup paksa")
    middle = httpx.ConnectError("")
    middle.__cause__ = root
    outer = httpx.ConnectError("")
    outer.__cause__ = middle

    assert _error_text(outer) == ("ConnectError: [WinError 10054] koneksi ditutup paksa")


def test_error_text_collapses_multiline_messages() -> None:
    """Pesan httpx untuk status error memuat baris tambahan yang mengotori log."""
    response = httpx.Response(500, request=httpx.Request("GET", "https://example.test/a.pdf"))
    error = httpx.HTTPStatusError(
        "boom\n\nFor more information check: ...", request=response.request, response=response
    )

    assert "\n" not in _error_text(error)
