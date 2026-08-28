from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import replace
from urllib.parse import urlencode

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..domain.models import SearchItem, SourceDocument
from ..services.hierarchy import (
    legal_hierarchy_label,
    legal_hierarchy_rank,
    prioritize_legal_hierarchy,
)
from ..services.ministry import matches_ministry
from .parsing import ParseError, matched_keywords, parse_detail_page, parse_search_page

LOGGER = logging.getLogger(__name__)


class JdihnRequestError(RuntimeError):
    pass


class JdihnClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        request_delay_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._delay = request_delay_seconds
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        # WAF JDIHN memutus TLS fingerprint milik OpenSSL-nya Python, sehingga permintaan
        # halaman dilempar ke curl bawaan sistem yang memakai tumpukan TLS berbeda.
        # Pencariannya tidak lagi terbatas Windows: container Linux juga menyediakan curl,
        # dan httpx adalah jalur yang justru diputus WAF.
        self._curl_path = (
            (shutil.which("curl") or shutil.which("curl.exe")) if client is None else None
        )
        self._use_native_curl = self._curl_path is not None
        if client is None and self._curl_path is None:
            LOGGER.warning(
                "curl tidak ditemukan pada PATH; permintaan JDIHN memakai httpx dan "
                "berisiko diputus WAF"
            )
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "AlignmentIntelligenceCollector/0.1 (+https://alignment-intelligence.apps.arulize.com)"
            },
        )
        self._has_requested = False

    async def __aenter__(self) -> JdihnClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, str] | None = None) -> str:
        if path.startswith("/search-api"):
            raise ValueError("Endpoint /search-api/ tidak boleh digunakan")
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type((httpx.TransportError, JdihnRequestError)),
            reraise=True,
        ):
            with attempt:
                # Jeda kesopanan berlaku untuk setiap percobaan, termasuk retry. Kalau
                # hanya dipasang sebelum loop, retry menembak di jarak backoff saja dan
                # justru memperparah reset koneksi dari WAF JDIHN.
                if self._has_requested:
                    await asyncio.sleep(self._delay)
                self._has_requested = True
                if self._use_native_curl:
                    return await self._get_with_native_curl(path, params)
                response = await self._client.get(f"{self._base_url}{path}", params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise JdihnRequestError(f"JDIHN mengembalikan HTTP {response.status_code}")
                response.raise_for_status()
                return response.text
        raise AssertionError("retry loop selesai tanpa response")

    async def _get_with_native_curl(self, path: str, params: dict[str, str] | None) -> str:
        if self._curl_path is None:
            raise JdihnRequestError("curl.exe tidak ditemukan pada PATH")
        query = f"?{urlencode(params)}" if params else ""
        command = (
            self._curl_path,
            "--ssl-no-revoke",
            "--http1.1",
            "--ipv4",
            "--silent",
            "--show-error",
            "--location",
            "--max-redirs",
            "3",
            "--fail-with-body",
            "--compressed",
            "--connect-timeout",
            str(self._timeout_seconds),
            "--max-time",
            str(self._timeout_seconds),
            "--user-agent",
            "AlignmentIntelligenceCollector/0.1 (+https://alignment-intelligence.apps.arulize.com)",
            f"{self._base_url}{path}{query}",
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise JdihnRequestError(message or f"curl.exe exit code {process.returncode}")
        try:
            return stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise JdihnRequestError("Response JDIHN bukan UTF-8 valid") from error

    async def discover(
        self,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        *,
        ministry: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> list[SearchItem]:
        items_by_id: dict[str, SearchItem] = {}
        successful_queries = 0
        last_request_error: Exception | None = None
        for keyword in keywords:
            LOGGER.info("Mencari kandidat JDIHN untuk keyword=%s", keyword)
            try:
                page = await self._get("/search", {"q": keyword})
            except (httpx.TransportError, JdihnRequestError) as error:
                last_request_error = error
                LOGGER.warning(
                    "Pencarian JDIHN gagal untuk keyword=%s, lanjut keyword berikutnya: %s",
                    keyword,
                    error,
                )
                continue
            successful_queries += 1
            for item in parse_search_page(page):
                if item.category.casefold() != "peraturan":
                    continue
                if legal_hierarchy_rank(item) is None:
                    continue
                if not matched_keywords(item.searchable_text, keywords, exclude_keywords):
                    continue
                items_by_id.setdefault(item.source_id, item)
        if successful_queries == 0 and last_request_error is not None:
            raise JdihnRequestError("Semua pencarian keyword JDIHN gagal") from last_request_error
        prioritized = prioritize_legal_hierarchy(
            list(items_by_id.values()), ministry=ministry, aliases=aliases
        )
        LOGGER.info(
            "Kandidat sesuai hierarki nasional/provinsi=%s dari hasil unik=%s",
            len(prioritized),
            len(items_by_id),
        )
        if ministry:
            matched = sum(1 for item in prioritized if matches_ministry(item, ministry, aliases))
            LOGGER.info(
                "Kandidat instansi=%s: prioritas=%s, nasional lain=%s",
                ministry,
                matched,
                len(prioritized) - matched,
            )
            if matched == 0:
                # Sinyal bahwa nama kementerian yang tersimpan sudah tidak cocok dengan
                # nilai `instansi` milik JDIHN, biasanya setelah nomenklatur kabinet
                # berubah. Terlihat langsung oleh user yang menjalankan run.
                LOGGER.warning(
                    "Tidak ada kandidat dengan instansi=%s; "
                    "periksa apakah nama kementerian masih sesuai penulisan JDIHN",
                    ministry,
                )
        return prioritized

    async def fetch_detail(
        self,
        item: SearchItem,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
    ) -> SourceDocument:
        path = f"/doc/{item.source_id}"
        page = await self._get(path)
        document = parse_detail_page(
            page,
            item,
            f"{self._base_url}{path}",
            keywords,
            exclude_keywords,
        )
        # Dihitung di sini, bukan di parsing.py: hierarchy.py sudah mengimpor parsing.py,
        # sehingga arah sebaliknya akan membuat impor melingkar.
        return replace(document, legal_hierarchy=legal_hierarchy_label(item))


__all__ = ["JdihnClient", "JdihnRequestError", "ParseError"]
