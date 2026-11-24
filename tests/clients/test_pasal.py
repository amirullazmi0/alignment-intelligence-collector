"""Fixture di sini meniru respons nyata pasal.id dan situs sumbernya (24 Nov 2026)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.clients.parsing import ParseError
from app.clients.pasal import (
    SOURCE_ID_PREFIX,
    PasalAuthError,
    PasalClient,
    frbr_slug,
    parse_search_result,
    perda_region,
)
from app.clients.source_pages import find_pdf_url, is_official_url

PERATURAN_GO_ID_HTML = """
<table>
<tr><th style='width: 30%;'>Pemrakarsa</th><td>PEMERINTAH PUSAT</td></tr>
<tr><th style='width: 30%;'>Dokumen Peraturan</th><td>
<a href="/files/UU+13-2003pjl.pdf"><img alt=""></a>&nbsp;
<a href="/files/UU+13-2003.pdf"><img alt=""></a>&nbsp;</td></tr>
</table>
"""

BPK_HTML = """
<a class="btn" href="/Read/407335/permenkes-no-1-tahun-2026.pdf">Preview</a>
<a class="download-file btn" data-id="407335"
   href="/Download/407335/permenkes-no-1-tahun-2026.pdf">Download</a>
<a href="https://contoh.example/lain.pdf">bukan situs resmi</a>
"""

KEMKES_HTML = """
<iframe src="https://jdih.kemkes.go.id/pdfjs/web/viewer.html?file=x.pdf"></iframe>
<a href="https://jdih.kemkes.go.id/storage/documents/pdfs/2023permenkes0003.pdf">Unduh</a>
"""

WORK_PERMENKES: dict[str, Any] = {
    "frbr_uri": "/akn/id/act/permenkes/2023/3",
    "title": "Peraturan Menteri Nomor 3 Tahun 2023 tentang Standar Tarif Pelayanan Kesehatan",
    "number": "3",
    "year": 2023,
    "type": "PERMEN",
    "status": "berlaku",
}
WORK_UU: dict[str, Any] = {
    "frbr_uri": "/akn/id/act/uu/2014/36",
    "title": "Undang-Undang Nomor 36 Tahun 2014 tentang Tenaga Kesehatan",
    "number": "36",
    "year": 2014,
    "type": "UU",
    "status": "berlaku",
}
WORK_PERBAN: dict[str, Any] = {
    "frbr_uri": "/akn/id/act/peraturan-bpjs-kesehatan/2024/2",
    "title": "Peraturan BPJS Kesehatan Nomor 2 Tahun 2024",
    "number": "2",
    "year": 2024,
    "type": "PERBAN",
    "status": "berlaku",
}

DETAILS: dict[str, dict[str, Any]] = {
    "/api/v1/laws/akn/id/act/permenkes/2023/3": {
        **WORK_PERMENKES,
        "type_name": "Peraturan Menteri",
        "issuing_body": {"id": 18, "slug": "permenkes", "name": "Kementerian Kesehatan"},
        "source_url": "https://jdih.kemkes.go.id/documents/permenkes-3-2023",
        "url": "https://pasal.id/peraturan/permen/permenkes-no-3-tahun-2023",
    },
    "/api/v1/laws/akn/id/act/uu/2014/36": {
        **WORK_UU,
        "issuing_body": {"id": 128, "slug": "peraturan-dpr", "name": "DPR RI"},
        "source_url": "https://peraturan.go.id/id/uu-no-36-tahun-2014",
        "url": "https://pasal.id/peraturan/uu/uu-no-36-tahun-2014",
    },
}


def _result(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "work_id": 1,
        "score": 1.0,
        "work": work,
        "url": "https://pasal.id/peraturan/x",
        "matching_pasals": [{"pasal": "5", "url": "https://pasal.id/peraturan/x#pasal-5"}],
        "best_passage": {"pasal": "5", "url": "https://pasal.id/peraturan/x#pasal-5"},
    }


def test_find_pdf_url_peraturan_go_id_memilih_batang_tubuh() -> None:
    url = find_pdf_url(PERATURAN_GO_ID_HTML, "https://peraturan.go.id/id/uu-no-13-tahun-2003")
    assert url == "https://peraturan.go.id/files/UU+13-2003.pdf"


def test_find_pdf_url_bpk_memilih_download_bukan_preview() -> None:
    url = find_pdf_url(BPK_HTML, "https://peraturan.bpk.go.id/Details/344092/x")
    assert url == "https://peraturan.bpk.go.id/Download/407335/permenkes-no-1-tahun-2026.pdf"


def test_find_pdf_url_jdih_kementerian() -> None:
    url = find_pdf_url(KEMKES_HTML, "https://jdih.kemkes.go.id/documents/x")
    assert url == "https://jdih.kemkes.go.id/storage/documents/pdfs/2023permenkes0003.pdf"


def test_find_pdf_url_tanpa_pdf() -> None:
    assert find_pdf_url("<p>kosong</p>", "https://peraturan.go.id/id/x") is None


def test_is_official_url_hanya_https_go_id() -> None:
    assert is_official_url("https://peraturan.bpk.go.id/Details/1/x")
    assert not is_official_url("http://peraturan.go.id/id/x")
    assert not is_official_url("https://peraturan.go.id.evil.example/x")
    assert not is_official_url("https://pasal.id/peraturan/uu/x")


def test_parse_search_result_bentuk_asli() -> None:
    item = parse_search_result(_result(WORK_PERMENKES))

    assert item is not None
    assert item.source_id == f"{SOURCE_ID_PREFIX}akn/id/act/permenkes/2023/3"
    assert item.law_type == "Peraturan Menteri"
    assert item.document_number == "Nomor 3 Tahun 2023"
    assert item.institution is None
    assert item.description is None  # best_passage berupa object, bukan teks


def test_frbr_slug() -> None:
    assert frbr_slug("akn/id/act/permenkes/2023/3") == "permenkes"
    assert frbr_slug("/akn/id/act/uu/2014/36") == "uu"
    assert frbr_slug("bukan/frbr") is None


def _transport(seen: list[httpx.Request], *, search_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.url.host == "pasal.id" and path == "/api/v1/search":
            if search_status != 200:
                return httpx.Response(search_status, json={"error": "x"})
            by_type = {
                "PERMEN": [WORK_PERMENKES],
                "UU": [WORK_UU],
                # Meniru filter yang tidak dikenali: hasilnya jenis lain.
                "PERDA": [WORK_PERBAN],
            }
            works = by_type.get(request.url.params.get("type", ""), [])
            return httpx.Response(200, json={"results": [_result(work) for work in works]})
        if request.url.host == "pasal.id" and path in DETAILS:
            return httpx.Response(200, json={"work": DETAILS[path], "content": "x"})
        if request.url.host == "jdih.kemkes.go.id":
            return httpx.Response(200, text=KEMKES_HTML)
        if request.url.host == "peraturan.go.id":
            return httpx.Response(200, text=PERATURAN_GO_ID_HTML)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(http_client: httpx.AsyncClient) -> PasalClient:
    return PasalClient("https://pasal.id/api/v1", "token-uji", 5, 0, http_client)


@pytest.mark.asyncio
async def test_run_kementerian_memprioritaskan_permen_dan_mengambil_pdf_resmi() -> None:
    seen: list[httpx.Request] = []
    keywords = ("Kementerian Kesehatan", "tarif")
    async with httpx.AsyncClient(transport=_transport(seen)) as http_client:
        client = _client(http_client)
        items = await client.discover(keywords, (), ministry="Kementerian Kesehatan")
        permen = await client.fetch_detail(items[0], keywords, ())
        uu = await client.fetch_detail(items[1], keywords, ())

    # PERBAN tidak masuk hierarki; Permen milik Kemenkes didahulukan di atas UU.
    assert [item.source_id for item in items] == [
        "pasal:akn/id/act/permenkes/2023/3",
        "pasal:akn/id/act/uu/2014/36",
    ]
    assert items[0].institution == "Kementerian Kesehatan"

    assert permen.attachment_url == (
        "https://jdih.kemkes.go.id/storage/documents/pdfs/2023permenkes0003.pdf"
    )
    assert permen.institution == "Kementerian Kesehatan"
    assert permen.legal_hierarchy == "PERPRES_KEPPRES_PERMEN"
    assert permen.source_name == "jdih.kemkes.go.id (via pasal.id)"
    # Judul tidak memuat keyword, tapi pencariannya menemukan dokumen ini.
    assert permen.matched_keywords == ("Kementerian Kesehatan", "tarif")

    assert uu.attachment_url == "https://peraturan.go.id/files/UU+13-2003.pdf"
    assert uu.legal_hierarchy == "UU_PERPU"

    api = [request for request in seen if request.url.host == "pasal.id"]
    assert all(r.headers["Authorization"] == "Bearer token-uji" for r in api)
    # Hanya API pasal.id yang dibuka, dan token tidak pernah dikirim ke situs lain.
    assert all(r.url.path.startswith("/api/v1/") for r in api)
    assert all("Authorization" not in r.headers for r in seen if r.url.host != "pasal.id")
    # Detail Permen dipakai ulang dari tahap discovery, tidak diminta dua kali.
    detail_paths = [r.url.path for r in api if r.url.path.startswith("/api/v1/laws/")]
    assert detail_paths.count("/api/v1/laws/akn/id/act/permenkes/2023/3") == 1


@pytest.mark.asyncio
async def test_exclude_keyword_membuang_kandidat() -> None:
    async with httpx.AsyncClient(transport=_transport([])) as http_client:
        items = await _client(http_client).discover(("kesehatan",), ("tenaga kesehatan",))
    assert "pasal:akn/id/act/uu/2014/36" not in [item.source_id for item in items]


@pytest.mark.asyncio
async def test_token_ditolak_menghentikan_run() -> None:
    async with httpx.AsyncClient(transport=_transport([], search_status=401)) as http_client:
        with pytest.raises(PasalAuthError):
            await _client(http_client).discover(("kesehatan",), ())


@pytest.mark.asyncio
async def test_detail_tidak_ditemukan_menjadi_parse_error() -> None:
    item = parse_search_result(
        {"work": {"frbr_uri": "/akn/id/act/uu/1900/1", "title": "Undang-Undang X", "type": "UU"}}
    )
    assert item is not None
    async with httpx.AsyncClient(transport=_transport([])) as http_client:
        with pytest.raises(ParseError):
            await _client(http_client).fetch_detail(item, ("x",), ())


@pytest.mark.parametrize(
    ("frbr_uri", "expected"),
    [
        ("/akn/id/act/perda/prov-jawa-barat/2008/16", "Provinsi Jawa Barat"),
        ("/akn/id/act/perda-provinsi-jawa-barat/2008/16", "Provinsi Jawa Barat"),
        ("/akn/id/act/perda-prov-dki-jakarta/2026/1", "Provinsi Dki Jakarta"),
        ("/akn/id/act/perda-kab-bogor/2022/1", "Kabupaten Bogor"),
        ("/akn/id/act/perda-kabupaten-hulu-sungai-selatan/2009/6", "Kabupaten Hulu Sungai Selatan"),
        ("/akn/id/act/perda/kota-bandung/2016/15", "Kota Bandung"),
        ("/akn/id/act/uu/2014/36", None),
    ],
)
def test_perda_region(frbr_uri: str, expected: str | None) -> None:
    assert perda_region(frbr_uri) == expected


def test_perda_provinsi_lolos_hierarki() -> None:
    from app.services.hierarchy import legal_hierarchy_label

    item = parse_search_result(
        _result(
            {
                "frbr_uri": "/akn/id/act/perda/prov-jawa-barat/2019/10",
                "title": "Peraturan Daerah Nomor 10 Tahun 2019 tentang Kesehatan",
                "type": "PERDA",
                "year": 2019,
            }
        )
    )
    assert item is not None
    assert legal_hierarchy_label(item) == "PERDA_PROVINSI"
