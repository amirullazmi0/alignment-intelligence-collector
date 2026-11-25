from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.clients.peraturan_go_id import (
    DEFAULT_CATALOGUES,
    PeraturanGoIdClient,
    Relation,
    best_pdf,
    catalogues_from_keywords,
    parse_detail_page,
    parse_list_page,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "peraturan_go_id"
BASE = "https://peraturan.go.id"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_list_page_items_carry_title_number_year_and_pdf() -> None:
    listed = parse_list_page(_fixture("uu_page2.html"), BASE, "uu")
    assert len(listed) == 20
    item, pdf = next((i, p) for i, p in listed if i.source_id == "pgo:uu-no-4-tahun-2025")
    assert (
        item.title
        == "Undang-Undang Nomor 4 Tahun 2025 tentang Kabupaten Gorontalo di Provinsi Gorontalo"
    )
    assert item.document_number == "Nomor 4 Tahun 2025"
    assert item.law_year == 2025
    assert item.law_type == "Undang-Undang"
    assert pdf == "https://peraturan.go.id/files/uu-no-4-tahun-2025.pdf"
    assert all(i.source_id.startswith("pgo:uu-") for i, _ in listed)


def test_list_page_past_the_end_is_empty() -> None:
    assert parse_list_page("<html><body><p>Tidak ada data</p></body></html>", BASE, "uu") == []


def test_detail_page_status_issuer_pdfs_and_relations() -> None:
    detail = parse_detail_page(_fixture("uu_13_2003.html"), f"{BASE}/id/uu-no-13-tahun-2003")
    assert detail.status == "Berlaku"
    assert detail.issuer == "PEMERINTAH PUSAT"
    assert best_pdf(detail.pdf_urls) == "https://peraturan.go.id/files/UU+13-2003.pdf"
    assert Relation("Mencabut", "Undang-Undang Nomor 25 Tahun 1997") in detail.relations
    # Each relation once, even though the page repeats the list.
    assert len(detail.relations) == len(set(detail.relations))


def test_catalogues_from_schedule_keywords() -> None:
    assert catalogues_from_keywords(("UU", "pp", "kesehatan")) == ("uu", "pp")
    assert catalogues_from_keywords(("kesehatan",)) == DEFAULT_CATALOGUES


def _page(items: list[str]) -> str:
    cards = "".join(
        f'<div class="strip grid"><p>Undang-Undang Nomor {n} Tahun 2020</p>'
        f'<p><a href="/id/uu-no-{n}-tahun-2020">Hal {n}</a></p>'
        f'<a href="/files/uu-no-{n}-tahun-2020.pdf">pdf</a></div>'
        for n in items
    )
    return f"<html><body>{cards}</body></html>"


@pytest.mark.asyncio
async def test_sweep_moves_the_checkpoint_only_past_fully_known_pages() -> None:
    pages = {"1": ["1", "2"], "2": ["3", "4"], "3": []}
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        requested.append(page)
        return httpx.Response(200, text=_page(pages[page]))

    known = {"pgo:uu-no-1-tahun-2020", "pgo:uu-no-2-tahun-2020"}
    checkpoints: dict[str, int] = {}

    async def is_known(source_id: str) -> bool:
        return source_id in known

    async def get_checkpoint(catalogue: str) -> int:
        return checkpoints.get(catalogue, 1)

    async def set_checkpoint(catalogue: str, page: int) -> None:
        checkpoints[catalogue] = page

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PeraturanGoIdClient(
            BASE,
            5,
            0,
            mode="backfill",
            is_known=is_known,
            get_checkpoint=get_checkpoint,
            set_checkpoint=set_checkpoint,
            client=http,
        )
        found = await client.discover(("uu",), ())

    # Page 1 is fully known: checkpoint moves to 2. Page 2 holds new items, so the
    # checkpoint stays there until they are recorded; page 3 is past the end.
    assert [i.source_id for i in found] == ["pgo:uu-no-3-tahun-2020", "pgo:uu-no-4-tahun-2020"]
    assert checkpoints["uu"] == 1  # end reached: the next sweep starts over from page 1
    assert requested == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_incremental_reads_only_the_first_pages() -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.params["page"])
        return httpx.Response(200, text=_page(["9"]))

    async def no(_: str) -> bool:
        return False

    async def one(_: str) -> int:
        return 50

    async def noop(_: str, __: int) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = PeraturanGoIdClient(
            BASE,
            5,
            0,
            mode="incremental",
            is_known=no,
            get_checkpoint=one,
            set_checkpoint=noop,
            client=http,
        )
        await client.discover(("uu",), (), wanted=100)
    assert requested == ["1", "2"]
