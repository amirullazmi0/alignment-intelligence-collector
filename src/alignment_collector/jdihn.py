from __future__ import annotations

import asyncio
import logging

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

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
        self._owns_client = client is None
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
        if self._has_requested:
            await asyncio.sleep(self._delay)
        self._has_requested = True

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.TransportError, JdihnRequestError)),
            reraise=True,
        ):
            with attempt:
                response = await self._client.get(f"{self._base_url}{path}", params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise JdihnRequestError(f"JDIHN mengembalikan HTTP {response.status_code}")
                response.raise_for_status()
                return response.text
        raise AssertionError("retry loop selesai tanpa response")

    async def discover(
        self,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
    ) -> list[SearchItem]:
        items_by_id: dict[str, SearchItem] = {}
        for keyword in keywords:
            LOGGER.info("Mencari kandidat JDIHN untuk keyword=%s", keyword)
            page = await self._get("/search", {"q": keyword})
            for item in parse_search_page(page):
                if item.category.casefold() != "peraturan":
                    continue
                if not matched_keywords(item.searchable_text, keywords, exclude_keywords):
                    continue
                items_by_id.setdefault(item.source_id, item)
        return list(items_by_id.values())

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
