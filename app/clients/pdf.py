from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
import ssl
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
import truststore

from ..domain.models import DownloadedPdf


class PdfDownloadError(RuntimeError):
    pass


class UnsafePdfUrlError(PdfDownloadError):
    pass


def _safe_filename(value: str) -> str:
    decoded = unquote(value).strip()
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", decoded).strip("._")
    if not sanitized:
        sanitized = "document.pdf"
    if not sanitized.casefold().endswith(".pdf"):
        sanitized += ".pdf"
    return sanitized[-180:]


async def _validate_public_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafePdfUrlError("URL PDF harus HTTPS publik tanpa credential")

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise UnsafePdfUrlError("Host PDF tidak dapat di-resolve") from error

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafePdfUrlError("URL PDF mengarah ke jaringan non-publik")


class PdfDownloader:
    def __init__(
        self,
        timeout_seconds: float,
        max_pdf_bytes: int,
        client: httpx.AsyncClient | None = None,
        download_dir: Path = Path("downloads"),
    ) -> None:
        self._max_pdf_bytes = max_pdf_bytes
        self._download_dir = download_dir
        self._owns_client = client is None
        ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            verify=ssl_context,
            headers={"User-Agent": "AlignmentIntelligenceCollector/0.1"},
        )

    async def __aenter__(self) -> PdfDownloader:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def download(self, url: str, fallback_filename: str) -> DownloadedPdf:
        current_url = url
        for redirect_count in range(4):
            await _validate_public_https_url(current_url)
            async with self._client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count == 3:
                        raise PdfDownloadError("Redirect PDF tidak valid atau terlalu banyak")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                return await self._save_response(response, current_url, fallback_filename)
        raise PdfDownloadError("Redirect PDF terlalu banyak")

    async def _save_response(
        self,
        response: httpx.Response,
        url: str,
        fallback_filename: str,
    ) -> DownloadedPdf:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
        if content_type not in {"application/pdf", "application/octet-stream"}:
            raise PdfDownloadError(f"Content-Type bukan PDF: {content_type or 'missing'}")

        content_length = response.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self._max_pdf_bytes
        ):
            raise PdfDownloadError("PDF melebihi batas ukuran")

        disposition = response.headers.get("content-disposition", "")
        filename_match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
        url_name = Path(urlparse(url).path).name
        filename = _safe_filename(
            filename_match.group(1) if filename_match else url_name or fallback_filename
        )
        digest = hashlib.sha256()
        size = 0
        prefix = b""
        self._download_dir.mkdir(parents=True, exist_ok=True)
        path = self._download_dir / _safe_filename(fallback_filename)
        partial_path = path.with_suffix(f"{path.suffix}.part")
        try:
            with partial_path.open("wb") as temporary:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_pdf_bytes:
                        raise PdfDownloadError("PDF melebihi batas ukuran")
                    if len(prefix) < 5:
                        prefix += chunk[: 5 - len(prefix)]
                    digest.update(chunk)
                    temporary.write(chunk)
            if prefix != b"%PDF-":
                raise PdfDownloadError("Isi file tidak memiliki signature PDF")
            partial_path.replace(path)
            return DownloadedPdf(
                path=path,
                filename=filename,
                sha256=digest.hexdigest(),
                size_bytes=size,
                content_type=content_type,
            )
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise
