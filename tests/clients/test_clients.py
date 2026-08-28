from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.clients.alignment import AlignmentClient
from app.clients.jdihn import JdihnClient, JdihnRequestError
from app.clients.pdf import PdfDownloader, PdfDownloadError, UnsafePdfUrlError
from app.config import CollectorSettings
from app.domain.models import SourceDocument


def _flight_html(payload: str) -> str:
    return f"<html><script>self.__next_f.push({json.dumps([1, payload])})</script></html>"


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


@pytest.mark.parametrize(
    ("configured_url", "expected_url"),
    [
        ("www.jdihn.go.id", "https://www.jdihn.go.id"),
        ("https://jdihn.go.id", "https://www.jdihn.go.id"),
    ],
)
def test_settings_normalize_jdihn_url(
    monkeypatch: pytest.MonkeyPatch,
    configured_url: str,
    expected_url: str,
) -> None:
    monkeypatch.setenv("JDIHN_BASE_URL", configured_url)

    settings = CollectorSettings(_env_file=None)

    assert settings.jdihn_base_url == expected_url


@pytest.mark.asyncio
async def test_alignment_reauthenticates_once_after_401(tmp_path: Path) -> None:
    login_count = 0
    upload_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, upload_count
        if request.url.path.endswith("/v1/auth/login"):
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
        if request.url.path.endswith("/v1/auth/login"):
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
async def test_alignment_retries_without_law_year_for_legacy_api(tmp_path: Path) -> None:
    upload_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_count
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        upload_count += 1
        if b'name="lawYear"' in request.content:
            return httpx.Response(400, json={"message": ["lawYear must be an integer number"]})
        return httpx.Response(201, json={"data": {"documentId": "document-id"}})

    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AlignmentClient(
            "https://alignment.example", "bot@example.test", "secret", 5, http_client
        )
        result = await client.upload(_document(), pdf, "document.pdf")

    assert result.document_id == "document-id"
    assert upload_count == 2


@pytest.mark.asyncio
async def test_pdf_download_validates_magic_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_test_host(_url: str) -> None:
        return None

    monkeypatch.setattr("app.clients.pdf._validate_public_https_url", allow_test_host)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"not-pdf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        downloader = PdfDownloader(5, 1024, http_client, tmp_path)
        with pytest.raises(PdfDownloadError, match="signature"):
            await downloader.download("https://files.example.org/test.pdf", "test.pdf")


@pytest.mark.asyncio
async def test_pdf_rejects_non_https_url(tmp_path: Path) -> None:
    downloader = PdfDownloader(5, 1024, download_dir=tmp_path)
    try:
        with pytest.raises(UnsafePdfUrlError):
            await downloader.download("http://127.0.0.1/test.pdf", "test.pdf")
    finally:
        await downloader.__aexit__()


@pytest.mark.asyncio
async def test_pdf_download_persists_in_configured_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_test_host(_url: str) -> None:
        return None

    monkeypatch.setattr("app.clients.pdf._validate_public_https_url", allow_test_host)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-test",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        downloader = PdfDownloader(5, 1024, http_client, tmp_path)
        downloaded = await downloader.download(
            "https://files.example.org/original.pdf",
            "jdihn-42.pdf",
        )

    assert downloaded.path == tmp_path / "jdihn-42.pdf"
    assert downloaded.path.read_bytes() == b"%PDF-test"


@pytest.mark.asyncio
async def test_jdihn_client_discovers_and_fetches_detail_with_injected_http() -> None:
    item = {
        "id": 42,
        "title": "Peraturan Pendidikan",
        "documentNumber": "Nomor 7 Tahun 2024",
        "category": "peraturan",
        "subtitle": "Sekolah",
        "regulationType": "PERATURAN MENTERI",
        "tahunTerbit": 2024,
    }
    search_payload = {"mode": "search", "results": {"total": 1, "items": [item]}}
    detail_payload = {
        "id": 42,
        "canonicalTitle": "Peraturan Pendidikan Nomor 7 Tahun 2024",
        "documentNumber": "Nomor 7 Tahun 2024",
        "attachmentUrl": "https://files.example.org/42.pdf",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            body = _flight_html(f'1:["$",null,{{"initialData":{json.dumps(search_payload)}}}]')
            return httpx.Response(200, text=body)
        body = _flight_html(f'2:["$",null,{{"document":{json.dumps(detail_payload)}}}]')
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = JdihnClient("https://jdihn.go.id", 5, 0.5, http_client)
        items = await client.discover(("pendidikan",), ())
        document = await client.fetch_detail(items[0], ("pendidikan",), ())

    assert document.source_id == "42"
    assert document.attachment_url == "https://files.example.org/42.pdf"


@pytest.mark.asyncio
async def test_jdihn_discovery_keeps_results_when_one_keyword_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {
        "id": 42,
        "title": "Peraturan Pendidikan",
        "documentNumber": "Nomor 7 Tahun 2024",
        "category": "peraturan",
        "subtitle": "Sekolah",
        "regulationType": "PERATURAN MENTERI",
        "tahunTerbit": 2024,
    }
    payload = {"mode": "search", "results": {"total": 1, "items": [item]}}
    body = _flight_html(f'1:["$",null,{{"initialData":{json.dumps(payload)}}}]')
    client = JdihnClient("https://www.jdihn.go.id", 5, 0.5, httpx.AsyncClient())

    async def fake_get(_path: str, params: dict[str, str] | None = None) -> str:
        if params == {"q": "universitas"}:
            raise JdihnRequestError("connection reset")
        return body

    monkeypatch.setattr(client, "_get", fake_get)
    try:
        items = await client.discover(("pendidikan", "universitas"), ())
    finally:
        await client.__aexit__()

    assert [item.source_id for item in items] == ["42"]
