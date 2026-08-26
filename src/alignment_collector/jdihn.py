from __future__ import annotations

import asyncio
import logging
import os
import shutil
from urllib.parse import urlencode

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .hierarchy import legal_hierarchy_rank, prioritize_legal_hierarchy
from .models import SearchItem, SourceDocument
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
        self._use_native_curl = client is None and os.name == "nt"
        self._curl_path = shutil.which("curl.exe") if self._use_native_curl else None
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
        prioritized = prioritize_legal_hierarchy(list(items_by_id.values()))
        LOGGER.info(
            "Kandidat sesuai hierarki nasional/provinsi=%s dari hasil unik=%s",
            len(prioritized),
            len(items_by_id),
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
        return parse_detail_page(
            page,
            item,
            f"{self._base_url}{path}",
            keywords,
            exclude_keywords,
        )


__all__ = ["JdihnClient", "JdihnRequestError", "ParseError"]
