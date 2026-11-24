"""Sumber kedua: API metadata pasal.id, dengan PDF dari situs resmi `.go.id`.

Pasal.id hanya dipakai lewat API publiknya (token gratis, `Authorization: Bearer`), dan
API itu memang hanya mengembalikan metadata. Ketentuan pasal.id melarang ekstraksi
konten otomatis massal tanpa lisensi tertulis, dan robots.txt-nya menutup `/cari` serta
`/api/pdf/`, jadi client ini tidak pernah membuka halaman atau PDF pasal.id. Yang diambil
dari pasal.id hanya kandidat dan `source_url`-nya; PDF diunduh dari situs pemerintah yang
ditunjuk `source_url` (peraturan.go.id, peraturan.bpk.go.id, atau JDIH kementerian).

Bentuk respons yang dipakai di sini diambil dari pemanggilan nyata, bukan hanya dari
dokumentasinya:

- `GET /search?q&type&limit` -> `{"query", "total", "results": [{"work": {"frbr_uri":
  "/akn/id/act/permenkes/2023/3", "title", "number", "year", "type", "status"}, "url",
  "matching_pasals", "best_passage": {"pasal", "url"}}]}`. Tidak ada pemrakarsa, dan
  judul Permen sering hanya "Peraturan Menteri Nomor 3 Tahun 2023 tentang ..." tanpa
  nama menterinya.
- `GET /laws/{frbr_uri}` -> `{"work": {..., "type_name", "issuing_body": {"slug",
  "name", "abbreviation"} | null, "source_url", "url"}}`.
- Filter `type` yang tidak dikenal (mis. `PERDA_PROV`) diabaikan diam-diam: hasilnya
  campuran jenis lain. Perda memakai kode `PERDA`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import quote, urlparse

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..domain.models import SearchItem, SourceDocument
from ..services.hierarchy import (
    legal_hierarchy_label,
    legal_hierarchy_rank,
    prioritize_legal_hierarchy,
    title_states_form,
)
from ..services.ministry import matches_ministry
from .parsing import ParseError, matched_keywords
from .source_pages import find_pdf_url, is_official_url

LOGGER = logging.getLogger(__name__)

USER_AGENT = "AlignmentIntelligenceCollector/0.1 (+https://alignment-intelligence.apps.arulize.com)"
SOURCE_ID_PREFIX = "pasal:"
#: Batas `limit` endpoint search pasal.id.
SEARCH_PAGE_SIZE = 20

#: Jenis yang dicari per keyword. Satu pencarian per jenis membuat jenis yang jarang
#: muncul di peringkat atas (PP, Perpres) tetap mendapat jatah 20 hasilnya sendiri.
SEARCH_TYPES: tuple[str, ...] = ("UU", "PERPPU", "PP", "PERPRES", "PERMEN", "PERDA")

#: Jenis yang diterbitkan kementerian, sehingga pemrakarsanya perlu dicari tahu untuk
#: memprioritaskan kementerian yang dipilih. UU/PP/Perpres selalu DPR atau Presiden.
MINISTRY_TYPES = frozenset({"PERMEN", "KEPMEN"})

#: Kode jenis pasal.id -> label yang dikenali `legal_hierarchy_rank`. Jenis di luar daftar
#: ini (PERBAN, SE, POJK, dll.) dibiarkan berupa kodenya dan tersaring filter hierarki.
LAW_TYPE_LABELS: dict[str, str] = {
    "UUD": "Undang-Undang Dasar 1945",
    "TAP_MPR": "Ketetapan MPR",
    "UU": "Undang-Undang",
    "PERPPU": "Peraturan Pemerintah Pengganti Undang-Undang",
    "PP": "Peraturan Pemerintah",
    "PERPRES": "Peraturan Presiden",
    "KEPPRES": "Keputusan Presiden",
    "PERMEN": "Peraturan Menteri",
    "KEPMEN": "Keputusan Menteri",
    "PERDA": "Peraturan Daerah",
}


class PasalRequestError(RuntimeError):
    """Kegagalan sementara: 429, 5xx, atau koneksi."""


class PasalAuthError(RuntimeError):
    """Token pasal.id kosong, salah, atau dicabut. Tidak di-retry."""


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _year(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def law_type_label(type_code: str | None) -> str | None:
    if not type_code:
        return None
    return LAW_TYPE_LABELS.get(type_code.strip().upper(), type_code.strip())


def ministry_query(ministry: str) -> str | None:
    """Kata kunci Permen sebuah kementerian, mis. "Peraturan Menteri Kesehatan"."""
    prefix = "kementerian "
    if not ministry.casefold().startswith(prefix):
        return None
    rest = ministry[len(prefix) :].strip()
    return f"Peraturan Menteri {rest}" if rest else None


def frbr_uri_of(item: SearchItem) -> str:
    if not item.source_id.startswith(SOURCE_ID_PREFIX):
        raise ValueError(f"source_id {item.source_id} bukan milik pasal.id")
    return item.source_id[len(SOURCE_ID_PREFIX) :]


def frbr_slug(frbr_uri: str) -> str | None:
    """`akn/id/act/permenkes/2023/3` -> `permenkes`, sama dengan `issuing_body.slug`."""
    parts = frbr_uri.strip("/").split("/")
    return parts[3] if len(parts) > 3 and parts[:3] == ["akn", "id", "act"] else None


_PERDA_REGION = re.compile(
    r"^perda[-/](?P<level>prov|provinsi|kab|kabupaten|kota)-(?P<name>[a-z0-9-]+?)(?:/|$)"
)
_PERDA_LEVELS = {
    "prov": "Provinsi",
    "provinsi": "Provinsi",
    "kab": "Kabupaten",
    "kabupaten": "Kabupaten",
    "kota": "Kota",
}


def perda_region(frbr_uri: str) -> str | None:
    """Daerah penerbit Perda dari frbr_uri, mis. "Provinsi Jawa Barat".

    Judul Perda di pasal.id sering hanya "Peraturan Daerah Nomor 6 Tahun 2022 tentang ...",
    padahal filter hierarki butuh tahu provinsi atau kabupaten/kota. frbr_uri-nya selalu
    memuatnya, dalam beberapa ejaan: `perda/prov-jawa-barat`, `perda-provinsi-jawa-barat`,
    `perda-kab-bogor`, `perda/kota-bandung`.
    """
    parts = frbr_uri.strip("/").split("/")
    if len(parts) < 4 or parts[:3] != ["akn", "id", "act"]:
        return None
    match = _PERDA_REGION.match("/".join(parts[3:5]).casefold())
    if not match:
        return None
    name = match.group("name").replace("-", " ").title()
    return f"{_PERDA_LEVELS[match.group('level')]} {name}"


def _slug_ranks(slug: str) -> frozenset[int]:
    """Rank yang wajar untuk sebuah slug frbr_uri; kosong berarti di luar cakupan."""
    if slug == "uud":
        return frozenset({1})
    if slug.startswith("tap"):
        return frozenset({2})
    if slug in {"uu", "perppu", "perpu"}:
        return frozenset({3})
    if slug == "pp":
        return frozenset({4})
    if slug == "perpres":
        return frozenset({5})
    if slug.startswith("permen") or slug == "pmk":
        return frozenset({5})
    if slug.startswith("perda"):
        return frozenset({6, 7})
    return frozenset()


def in_scope(item: SearchItem) -> bool:
    """Cakupan Perda sampai UUD, dengan dua pengaman khusus pasal.id.

    Label `type` pasal.id kadang keliru (Permenkumham berlabel PP, Perwali di bawah
    Perda), jadi yang diterima hanya dokumen yang judulnya menyebut bentuknya, dan bentuk
    itu sesuai dengan slug frbr_uri-nya. Yang tidak sesuai dibuang, bukan ditebak.
    """
    rank = legal_hierarchy_rank(item)
    if rank is None or not title_states_form(item):
        return False
    slug = frbr_slug(frbr_uri_of(item))
    return slug is not None and rank in _slug_ranks(slug)


def _type_code(item: SearchItem) -> str | None:
    for code, label in LAW_TYPE_LABELS.items():
        if item.law_type == label:
            return code
    return item.law_type


def parse_search_result(
    result: Mapping[str, object], institution: str | None = None
) -> SearchItem | None:
    work = _mapping(result.get("work")) or result
    frbr_uri = _text(work.get("frbr_uri"))
    title = _text(work.get("title"))
    if not frbr_uri or not title:
        return None
    number = _text(work.get("number"))
    year = _year(work.get("year"))
    document_number = None
    if number:
        document_number = f"Nomor {number}" + (f" Tahun {year}" if year else "")
    return SearchItem(
        source_id=f"{SOURCE_ID_PREFIX}{frbr_uri.strip('/')}",
        title=title,
        document_number=document_number,
        category="Peraturan",
        subtitle=None,
        description=None,
        institution=institution or perda_region(frbr_uri),
        effective_status=_text(work.get("status")),
        law_type=law_type_label(_text(work.get("type"))),
        law_year=year,
    )


class PasalClient:
    def __init__(
        self,
        api_base_url: str,
        token: str,
        timeout_seconds: float,
        request_delay_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._token = token
        self._delay = request_delay_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._has_requested = False
        # Keyword yang menemukan tiap kandidat. Pencarian pasal.id mencocokkan isi pasal,
        # sedangkan metadata yang dikembalikan hanya judul, jadi mencocokkan keyword ke
        # judul akan membuang hampir semua hasil yang relevan.
        self._hits: dict[str, list[str]] = {}
        # Respons /laws per frbr_uri dan nama pemrakarsa per slug, supaya satu dokumen
        # tidak diminta dua kali dalam satu run.
        self._works: dict[str, Mapping[str, object]] = {}
        self._issuers: dict[str, str | None] = {}

    async def __aenter__(self) -> PasalClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self, url: str, *, params: dict[str, str] | None, api: bool
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}"} if api else None
        host = "pasal.id" if api else (urlparse(url).hostname or url)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            # Batas pasal.id dihitung per 60 detik, jadi backoff boleh sampai satu menit.
            wait=wait_exponential(multiplier=2, min=2, max=60),
            retry=retry_if_exception_type((httpx.TransportError, PasalRequestError)),
            reraise=True,
        ):
            with attempt:
                if self._has_requested:
                    await asyncio.sleep(self._delay)
                self._has_requested = True
                response = await self._client.get(url, params=params, headers=headers)
                if api and response.status_code in {401, 403}:
                    raise PasalAuthError(
                        f"pasal.id menolak token (HTTP {response.status_code}); "
                        "periksa PASAL_API_TOKEN"
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise PasalRequestError(f"{host} mengembalikan HTTP {response.status_code}")
                if 400 <= response.status_code < 500:
                    # 404 atau parameter ditolak: kandidat ini dilewati, bukan menghentikan run.
                    raise ParseError(f"HTTP {response.status_code} untuk {url}")
                response.raise_for_status()
                return response
        raise AssertionError("retry loop selesai tanpa response")

    async def _api_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> Mapping[str, object]:
        response = await self._request(f"{self._api_base_url}{path}", params=params, api=True)
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ParseError(f"Respons pasal.id {path} bukan JSON") from error
        mapping = _mapping(payload)
        if mapping is None:
            raise ParseError(f"Respons pasal.id {path} bukan object JSON")
        return mapping

    async def _work(self, frbr_uri: str) -> Mapping[str, object]:
        cached = self._works.get(frbr_uri)
        if cached is not None:
            return cached
        payload = await self._api_json(f"/laws/{quote(frbr_uri.strip('/'), safe='/')}")
        work = _mapping(payload.get("work"))
        if work is None:
            raise ParseError(f"Respons pasal.id /laws/{frbr_uri} tanpa work")
        self._works[frbr_uri] = work
        slug = frbr_slug(frbr_uri)
        if slug is not None:
            self._issuers[slug] = _issuer_name(work)
        return work

    async def _search(self, query: str, type_code: str) -> list[Mapping[str, object]]:
        payload = await self._api_json(
            "/search", {"q": query, "type": type_code, "limit": str(SEARCH_PAGE_SIZE)}
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise ParseError("Respons search pasal.id tanpa daftar results")
        return [item for item in (_mapping(raw) for raw in results) if item is not None]

    async def _resolve_issuers(self, items: list[SearchItem]) -> list[SearchItem]:
        """Isi `institution` Permen/Kepmen dari `issuing_body`, satu detail per slug.

        Pemrakarsa hanya ada di /laws, dan slug frbr_uri-nya (`permenkes`) sama untuk
        semua Permen satu kementerian, jadi cukup satu permintaan per slug.
        """
        resolved: list[SearchItem] = []
        for item in items:
            frbr_uri = frbr_uri_of(item)
            slug = frbr_slug(frbr_uri)
            if slug is None or _type_code(item) not in MINISTRY_TYPES:
                resolved.append(item)
                continue
            if slug not in self._issuers:
                try:
                    await self._work(frbr_uri)
                except (httpx.TransportError, PasalRequestError, ParseError) as error:
                    LOGGER.warning("Gagal membaca pemrakarsa slug=%s: %s", slug, error)
                    self._issuers[slug] = None
            resolved.append(replace(item, institution=self._issuers.get(slug)))
        return resolved

    async def discover(
        self,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        *,
        ministry: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> list[SearchItem]:
        queries: list[tuple[str, str, str]] = [
            (keyword, keyword, type_code) for keyword in keywords for type_code in SEARCH_TYPES
        ]
        if ministry and (permen_query := ministry_query(ministry)):
            queries.insert(0, (ministry, permen_query, "PERMEN"))

        items_by_id: dict[str, SearchItem] = {}
        successful_queries = 0
        last_error: Exception | None = None
        for keyword, query, type_code in queries:
            LOGGER.info("Mencari kandidat pasal.id q=%s type=%s", query, type_code)
            try:
                results = await self._search(query, type_code)
            except (httpx.TransportError, PasalRequestError, ParseError) as error:
                last_error = error
                LOGGER.warning(
                    "Pencarian pasal.id gagal q=%s type=%s, lanjut: %s", query, type_code, error
                )
                continue
            successful_queries += 1
            for result in results:
                item = parse_search_result(result)
                if item is None or not in_scope(item):
                    continue
                if matched_keywords(item.searchable_text, exclude_keywords, ()):
                    continue
                hits = self._hits.setdefault(item.source_id, [])
                if keyword not in hits:
                    hits.append(keyword)
                items_by_id.setdefault(item.source_id, item)
        if successful_queries == 0 and last_error is not None:
            raise PasalRequestError("Semua pencarian pasal.id gagal") from last_error

        candidates = list(items_by_id.values())
        if ministry:
            candidates = await self._resolve_issuers(candidates)
        prioritized = prioritize_legal_hierarchy(candidates, ministry=ministry, aliases=aliases)
        LOGGER.info(
            "Kandidat pasal.id sesuai hierarki=%s dari hasil unik=%s",
            len(prioritized),
            len(items_by_id),
        )
        if ministry:
            matched = sum(1 for item in prioritized if matches_ministry(item, ministry, aliases))
            LOGGER.info(
                "Kandidat pasal.id kementerian=%s: prioritas=%s, nasional lain=%s",
                ministry,
                matched,
                len(prioritized) - matched,
            )
        return prioritized

    async def fetch_detail(
        self,
        item: SearchItem,
        keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
    ) -> SourceDocument:
        frbr_uri = frbr_uri_of(item)
        work = await self._work(frbr_uri)
        source_url = _text(work.get("source_url"))
        pasal_url = _text(work.get("url"))

        attachment_url: str | None = None
        if source_url and is_official_url(source_url):
            page = await self._request(source_url, params=None, api=False)
            attachment_url = find_pdf_url(page.text, str(page.url))
            if attachment_url is None:
                LOGGER.info("Tidak ada PDF di %s untuk %s", source_url, frbr_uri)
        else:
            LOGGER.info(
                "source_url %s untuk %s bukan situs .go.id; dilewati tanpa PDF",
                source_url,
                frbr_uri,
            )

        title = _text(work.get("title")) or item.title
        law_type = law_type_label(_text(work.get("type"))) or item.law_type
        law_year = _year(work.get("year")) or item.law_year
        number = _text(work.get("number"))
        effective_status = _text(work.get("status")) or item.effective_status
        institution = _issuer_name(work) or item.institution
        document_number = item.document_number
        if number:
            document_number = f"Nomor {number}" + (f" Tahun {law_year}" if law_year else "")

        excluded = matched_keywords(
            " ".join(v for v in (title, institution) if v), exclude_keywords, ()
        )
        hits = tuple(k for k in self._hits.get(item.source_id, ()) if k in keywords)
        fingerprint_payload = json.dumps(
            {
                "attachment_url": attachment_url,
                "document_number": document_number,
                "effective_status": effective_status,
                "law_type": law_type,
                "law_year": law_year,
                "title": title,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_host = urlparse(source_url).hostname if source_url else None
        return SourceDocument(
            source_id=item.source_id,
            source_url=source_url or pasal_url or item.source_id,
            title=title,
            attachment_url=attachment_url,
            category=item.category,
            document_number=document_number,
            law_type=law_type,
            law_number=number,
            law_year=law_year,
            effective_status=effective_status,
            matched_keywords=() if excluded else hits,
            fingerprint=hashlib.sha256(fingerprint_payload.encode()).hexdigest(),
            institution=institution,
            legal_hierarchy=legal_hierarchy_label(item),
            source_name=f"{source_host} (via pasal.id)" if source_host else "pasal.id",
        )


def _issuer_name(work: Mapping[str, object]) -> str | None:
    body = _mapping(work.get("issuing_body"))
    return _text(body.get("name")) if body else None


__all__ = [
    "SOURCE_ID_PREFIX",
    "PasalAuthError",
    "PasalClient",
    "PasalRequestError",
    "frbr_slug",
    "parse_search_result",
]
