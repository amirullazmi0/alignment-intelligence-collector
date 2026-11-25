"""Cari tautan PDF di halaman sumber resmi yang ditunjuk `source_url` pasal.id.

Dari sampel API pasal.id (24 Nov 2026), `source_url` mengarah ke tiga jenis situs:

- peraturan.go.id: `/files/UU+13-2003.pdf`, kadang ditemani penjelasan `...pjl.pdf`;
- peraturan.bpk.go.id: `/Download/<id>/<slug>.pdf` plus salinan pratinjau `/Read/...`;
- JDIH kementerian, mis. jdih.kemkes.go.id: `/storage/documents/pdfs/<file>.pdf`.

Semuanya situs pemerintah (`.go.id`) dan robots.txt-nya mengizinkan halaman detail.
Daripada satu parser per situs, tautan PDF di halaman diberi peringkat: unduhan langsung
lebih dulu, pratinjau dan penjelasan paling belakang.
"""

from __future__ import annotations

from urllib.parse import unquote, urljoin, urlparse

from selectolax.parser import HTMLParser

OFFICIAL_SUFFIX = ".go.id"


def is_official_url(url: str) -> bool:
    """HTTPS di domain pemerintah `.go.id`; hanya situs seperti ini yang dibuka."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host.endswith(OFFICIAL_SUFFIX)


def _rank(url: str) -> int:
    path = unquote(urlparse(url).path).casefold()
    rank = 0 if "/download/" in path else 1
    if "/read/" in path or "viewer" in path:
        rank += 2
    if path.endswith("pjl.pdf") or "penjelasan" in path:
        rank += 4
    return rank


def find_pdf_url(page_html: str, page_url: str) -> str | None:
    """Tautan PDF terbaik di halaman, atau None kalau tidak ada.

    Hanya tautan di host yang sama dengan halamannya yang diterima, supaya PDF milik
    peraturan lain yang kebetulan ditautkan dari situs berbeda tidak ikut terambil.
    """
    page_host = (urlparse(page_url).hostname or "").lower()
    candidates: list[str] = []
    for anchor in HTMLParser(page_html).css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if (parsed.hostname or "").lower() != page_host or not is_official_url(absolute):
            continue
        if not parsed.path.casefold().endswith(".pdf"):
            continue
        if absolute not in candidates:
            candidates.append(absolute)
    if not candidates:
        return None
    # sorted() stabil: pada peringkat sama, urutan kemunculan di halaman dipertahankan.
    return sorted(candidates, key=_rank)[0]


__all__ = ["find_pdf_url", "is_official_url"]
