"""Third source: the official catalogue of peraturan.go.id (Kementerian Hukum).

Unlike JDIHN and pasal.id, this source is swept, not searched. Each regulation
type has a paginated list (`/uu?page=N`, 20 items a page, newest first) whose
items already carry the title, number, year, issuer and the official PDF
link; the detail page (`/id/uu-no-13-tahun-2003`) adds the status (Berlaku,
Dicabut, ...) and "Hubungan Antar Peraturan" (Mencabut, Diubah dengan, ...).
robots.txt allows everything; the sitemap is broken (HTTP 500), hence the lists.

A schedule's keywords name the types to sweep (`uu, perppu, pp, perpres`). A
checkpoint per type remembers the first page that still has something new:
it only moves past a page once every item on it is known to the state store,
so a run capped by MAX_UPLOADS_PER_RUN never skips documents. Incremental runs
read the first pages only, where new regulations appear.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..domain.models import SearchItem, SourceDocument
from ..services.hierarchy import legal_hierarchy_label, prioritize_legal_hierarchy
from .parsing import ParseError

LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AlignmentIntelligenceCollector/0.1; +https://alignment-intelligence.apps.arulize.com)"
SOURCE_ID_PREFIX = "pgo:"
SOURCE_NAME = "peraturan.go.id"
PAGE_SIZE = 20

#: Catalogue slug -> label read by the hierarchy filter. Only types inside the
#: Perda-UUD scope are listed; peraturan.go.id has no UUD or TAP MPR list.
CATALOGUES: dict[str, str] = {
    "uu": "Undang-Undang",
    "perppu": "Peraturan Pemerintah Pengganti Undang-Undang",
    "pp": "Peraturan Pemerintah",
    "perpres": "Peraturan Presiden",
    "permen": "Peraturan Menteri",
    "perda": "Peraturan Daerah",
}
#: Types swept when a schedule names none: the national levels, which the
#: corpus lacks most.
DEFAULT_CATALOGUES: tuple[str, ...] = ("uu", "perppu", "pp", "perpres")


class PeraturanGoIdRequestError(RuntimeError):
    """Temporary failure: 429, 5xx or connection."""


@dataclass(frozen=True, slots=True)
class Relation:
    """One line of "Hubungan Antar Peraturan".

    For example ("Mencabut", "Undang-Undang Nomor 25 Tahun 1997").
    """

    kind: str
    target: str


@dataclass(frozen=True, slots=True)
class DetailPage:
    status: str | None
    issuer: str | None
    pdf_urls: tuple[str, ...]
    relations: tuple[Relation, ...]


KnownFn = Callable[[str], Awaitable[bool]]
CheckpointGet = Callable[[str], Awaitable[int]]
CheckpointSet = Callable[[str, int], Awaitable[None]]


def catalogues_from_keywords(keywords: tuple[str, ...]) -> tuple[str, ...]:
    """Schedule keywords -> catalogue slugs; unknown words are ignored."""
    slugs = tuple(
        dict.fromkeys(k.strip().casefold() for k in keywords if k.strip().casefold() in CATALOGUES)
    )
    return slugs or DEFAULT_CATALOGUES


def _text(node: object) -> str:
    raw = node.text(separator=" ") if node is not None else ""  # type: ignore[attr-defined]
    return " ".join(raw.split())


def _pdf_rank(url: str) -> int:
    path = urlparse(url).path.casefold()
    # The explanation ("...pjl.pdf") and translations ("_terjemah.pdf") come last.
    return (2 if path.endswith("pjl.pdf") else 0) + (1 if "terjemah" in path else 0)


def best_pdf(urls: tuple[str, ...] | list[str]) -> str | None:
    return sorted(urls, key=_pdf_rank)[0] if urls else None


def parse_list_page(
    html: str, base_url: str, catalogue: str
) -> list[tuple[SearchItem, str | None]]:
    """Items of one catalogue page with their PDF link; empty means past the last page."""
    items: list[tuple[SearchItem, str | None]] = []
    tree = HTMLParser(html)
    for card in tree.css("div.strip"):
        # The year badge also links to "/id/#"; the detail link is the other one.
        link = next(
            (
                a
                for a in card.css('a[href^="/id/"]')
                if (a.attributes.get("href") or "").strip() not in {"/id/#", "/id/"}
            ),
            None,
        )
        if link is None:
            continue
        slug = (link.attributes.get("href") or "").strip().removeprefix("/id/").strip("/")
        if not slug:
            continue
        heading = next((_text(p) for p in card.css("p") if "Nomor" in _text(p)), "")
        about = _text(link)
        title = f"{heading} tentang {about}" if heading and about else heading or about
        year_node = card.css_first("a.wish_bt")
        year_text = _text(year_node)
        match = re.search(r"Nomor\s+(\S+)\s+Tahun\s+(\d{4})", heading)
        pdfs = [
            urljoin(base_url, a.attributes.get("href") or "")
            for a in card.css('a[href$=".pdf"]')
            if a.attributes.get("href")
        ]
        institution = _text(card.css_first(".loc_open")) or None
        items.append(
            (
                SearchItem(
                    source_id=f"{SOURCE_ID_PREFIX}{slug}",
                    title=title,
                    document_number=f"Nomor {match.group(1)} Tahun {match.group(2)}"
                    if match
                    else None,
                    category=CATALOGUES[catalogue],
                    subtitle=None,
                    description=None,
                    institution=institution,
                    effective_status=None,
                    law_type=CATALOGUES[catalogue],
                    law_year=int(year_text)
                    if year_text.isdigit()
                    else (int(match.group(2)) if match else None),
                ),
                best_pdf(pdfs),
            )
        )
    return items


_FIELD_LABELS = ("Jenis/Bentuk Peraturan", "Pemrakarsa", "Status")
_RELATION_HEADER = re.compile(r"^(.+?)\s*:$")
_RELATION_TARGET = re.compile(r"^(.+?\bNomor\s+\S+\s+Tahun\s+\d{4})$")


def parse_detail_page(html: str, page_url: str) -> DetailPage:
    """Status, issuer, PDF links and "Hubungan Antar Peraturan" of a detail page."""
    tree = HTMLParser(html)
    for node in tree.css("script, style"):
        node.decompose()
    lines = [
        " ".join(t.split())
        for t in (tree.body.text(separator="\n") if tree.body else "").split("\n")
    ]
    lines = [t for t in lines if t and t != "-->"]

    # "Status" is also the name of two tabs above the data, so the last label/value
    # pair wins.
    fields: dict[str, str] = {}
    for i, line in enumerate(lines[:-1]):
        if line in _FIELD_LABELS:
            value = lines[i + 1]
            if value not in _FIELD_LABELS and value != "Dokumen Peraturan":
                fields[line] = value

    relations: list[Relation] = []
    if "Hubungan Antar Peraturan" in lines:
        kind: str | None = None
        for line in lines[lines.index("Hubungan Antar Peraturan") + 1 :]:
            if header := _RELATION_HEADER.match(line):
                kind = header.group(1).strip()
                continue
            if kind and (target := _RELATION_TARGET.match(line)):
                relation = Relation(kind, target.group(1))
                if relation not in relations:
                    relations.append(relation)

    pdfs = tuple(
        dict.fromkeys(
            urljoin(page_url, a.attributes.get("href") or "")
            for a in tree.css('a[href$=".pdf"]')
            if (a.attributes.get("href") or "").startswith("/files")
        )
    )
    return DetailPage(
        status=fields.get("Status"),
        issuer=fields.get("Pemrakarsa"),
        pdf_urls=pdfs,
        relations=tuple(relations),
    )


class PeraturanGoIdClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        request_delay_seconds: float,
        *,
        mode: str,
        is_known: KnownFn,
        get_checkpoint: CheckpointGet,
        set_checkpoint: CheckpointSet,
        max_pages_per_run: int = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._delay = request_delay_seconds
        self._mode = mode
        self._is_known = is_known
        self._get_checkpoint = get_checkpoint
        self._set_checkpoint = set_checkpoint
        self._max_pages = max_pages_per_run
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._has_requested = False
        self._catalogue_of: dict[str, str] = {}
        self._list_pdf: dict[str, str | None] = {}

    async def __aenter__(self) -> PeraturanGoIdClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type((httpx.TransportError, PeraturanGoIdRequestError)),
            reraise=True,
        ):
            with attempt:
                if self._has_requested:
                    await asyncio.sleep(self._delay)
                self._has_requested = True
                response = await self._client.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise PeraturanGoIdRequestError(
                        f"peraturan.go.id HTTP {response.status_code} untuk {url}"
                    )
                if response.status_code >= 400:
                    raise ParseError(f"HTTP {response.status_code} untuk {url}")
                return response
        raise AssertionError("retry loop selesai tanpa response")

    async def _sweep(self, catalogue: str, wanted: int) -> list[SearchItem]:
        """New items of one catalogue, from its checkpoint page on."""
        incremental = self._mode == "incremental"
        page = 1 if incremental else await self._get_checkpoint(catalogue)
        found: list[SearchItem] = []
        for _ in range(2 if incremental else self._max_pages):
            response = await self._get(f"{self._base_url}/{catalogue}", {"page": str(page)})
            listed = parse_list_page(response.text, self._base_url, catalogue)
            for item, pdf in listed:
                self._list_pdf[item.source_id] = pdf
            items = [item for item, _ in listed]
            if not items:
                if not incremental:
                    LOGGER.info(
                        "Katalog %s selesai disusuri sampai halaman %s", catalogue, page - 1
                    )
                    await self._set_checkpoint(catalogue, 1)
                break
            new = [item for item in items if not await self._is_known(item.source_id)]
            if not new and not incremental and page == await self._get_checkpoint(catalogue):
                # Every item of this page is already recorded: never look at it again.
                await self._set_checkpoint(catalogue, page + 1)
            found.extend(new)
            if len(found) >= wanted:
                break
            page += 1
        LOGGER.info("Katalog %s: %s kandidat baru (sampai halaman %s)", catalogue, len(found), page)
        return found

    async def discover(
        self,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        *,
        ministry: str | None = None,
        aliases: tuple[str, ...] = (),
        wanted: int = 60,
    ) -> list[SearchItem]:
        candidates: list[SearchItem] = []
        for catalogue in catalogues_from_keywords(keywords):
            try:
                items = await self._sweep(catalogue, wanted)
            except (httpx.TransportError, PeraturanGoIdRequestError, ParseError) as error:
                LOGGER.warning("Katalog %s gagal disusuri, lanjut: %s", catalogue, error)
                continue
            for item in items:
                self._catalogue_of[item.source_id] = catalogue
            candidates.extend(items)
        return prioritize_legal_hierarchy(candidates, ministry=ministry, aliases=aliases)

    async def fetch_detail(
        self,
        item: SearchItem,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
    ) -> SourceDocument:
        slug = item.source_id.removeprefix(SOURCE_ID_PREFIX)
        page_url = f"{self._base_url}/id/{slug}"
        response = await self._get(page_url)
        detail = parse_detail_page(response.text, str(response.url))
        listed_pdf = self._list_pdf.get(item.source_id)
        attachment = best_pdf([*detail.pdf_urls, *([listed_pdf] if listed_pdf else [])])
        catalogue = self._catalogue_of.get(item.source_id, "")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "attachment_url": attachment,
                    "status": detail.status,
                    "title": item.title,
                    "relations": [[r.kind, r.target] for r in detail.relations],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        number = re.search(r"Nomor\s+(\S+)", item.document_number or "")
        enriched = replace(
            item, institution=detail.issuer or item.institution, effective_status=detail.status
        )
        return SourceDocument(
            source_id=item.source_id,
            source_url=page_url,
            title=item.title,
            attachment_url=attachment,
            category=item.category,
            document_number=item.document_number,
            law_type=item.law_type,
            law_number=number.group(1) if number else None,
            law_year=item.law_year,
            effective_status=detail.status,
            # The sweep is the theme: every in-scope regulation of the catalogue counts.
            matched_keywords=(f"katalog {catalogue}" if catalogue else "katalog peraturan.go.id",),
            fingerprint=fingerprint,
            institution=enriched.institution,
            legal_hierarchy=legal_hierarchy_label(enriched),
            source_name=SOURCE_NAME,
        )


__all__ = [
    "CATALOGUES",
    "DEFAULT_CATALOGUES",
    "SOURCE_ID_PREFIX",
    "DetailPage",
    "PeraturanGoIdClient",
    "PeraturanGoIdRequestError",
    "Relation",
    "best_pdf",
    "catalogues_from_keywords",
    "parse_detail_page",
    "parse_list_page",
]
