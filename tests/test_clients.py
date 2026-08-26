from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from alignment_collector.alignment import AlignmentClient
from alignment_collector.models import SourceDocument
from alignment_collector.pdf import PdfDownloader, PdfDownloadError, UnsafePdfUrlError


def _document() -> SourceDocument:
    return SourceDocument(
        source_id="42",
        source_url="https://jdihn.go.id/doc/42",
        title="Peraturan Pendidikan",
        attachment_url="https://files.example.org/42.pdf",
        category="peraturan",
        document_number="Nomor 7 Tahun 2024",
        law_type="PERATURAN MENTERI",
        law_number="7",
        law_year=2024,
        effective_status="Berlaku",
        matched_keywords=("pendidikan",),
        fingerprint="fingerprint",
    )


@pytest.mark.asyncio
async def test_alignment_reauthenticates_once_after_401(tmp_path: Path) -> None:
    login_count = 0
    upload_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, upload_count
        if request.url.path == "/v1/auth/login":
            login_count += 1
            return httpx.Response(200, json={"data": {"accessToken": f"token-{login_count}"}})
        upload_count += 1
        if request.headers.get("authorization") == "Bearer token-1":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(201, json={"data": {"documentId": "document-id"}})

    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AlignmentClient(
            "https://alignment.example/api", "bot@example.test", "secret", 5, http_client
        )
        result = await client.upload(_document(), pdf, "document.pdf")

    assert result.document_id == "document-id"
    assert login_count == 2
    assert upload_count == 2


@pytest.mark.asyncio
async def test_alignment_treats_conflict_as_duplicate(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/login":
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        return httpx.Response(409, json={"message": "duplicate"})

    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AlignmentClient(
            "https://alignment.example/api", "bot@example.test", "secret", 5, http_client
        )
        result = await client.upload(_document(), pdf, "document.pdf")

    assert result.duplicate is True


@pytest.mark.asyncio
async def test_pdf_download_validates_magic_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_test_host(_url: str) -> None:
        return None

    monkeypatch.setattr("alignment_collector.pdf._validate_public_https_url", allow_test_host)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"not-pdf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        downloader = PdfDownloader(5, 1024, http_client)
        with pytest.raises(PdfDownloadError, match="signature"):
            await downloader.download("https://files.example.org/test.pdf", "test.pdf")


@pytest.mark.asyncio
async def test_pdf_rejects_non_https_url() -> None:
    downloader = PdfDownloader(5, 1024)
    try:
        with pytest.raises(UnsafePdfUrlError):
            await downloader.download("http://127.0.0.1/test.pdf", "test.pdf")
    finally:
        await downloader.__aexit__()
