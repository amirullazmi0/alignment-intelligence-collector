from __future__ import annotations

import json

import pytest

from alignment_collector.models import SearchItem
from alignment_collector.parsing import ParseError, parse_detail_page, parse_search_page


def _flight_html(payload: str) -> str:
    return f"<html><script>self.__next_f.push({json.dumps([1, payload])})</script></html>"


def test_parse_search_and_detail_payload() -> None:
    initial = {
        "mode": "search",
        "results": {
            "total": 1,
            "items": [
                {
                    "id": 42,
                    "title": "Peraturan Pendidikan Nasional",
                    "documentNumber": "Peraturan Nomor 7 Tahun 2024",
                    "category": "peraturan",
                    "subtitle": "Penyelenggaraan sekolah",
                    "description": "Aturan guru dan sekolah",
                    "instansi": "JDIH Contoh",
                    "status": "Berlaku",
                    "regulationType": "PERATURAN MENTERI",
                    "tahunTerbit": 2024,
                }
            ],
        },
    }
    search_html = _flight_html(f'13:["$",null,{{"initialData":{json.dumps(initial)}}}]')
    items = parse_search_page(search_html)
    assert len(items) == 1
    assert items[0].source_id == "42"

    detail_payload = {
        "id": 42,
        "canonicalTitle": "Peraturan Pendidikan Nasional Nomor 7 Tahun 2024",
        "documentNumber": "Peraturan Nomor 7 Tahun 2024",
        "effectiveStatus": "Berlaku",
        "type": "PERATURAN MENTERI",
        "enactedDate": "2024-01-10T00:00:00+07:00",
        "attachmentUrl": "https://files.example.org/peraturan.pdf",
    }
    detail_html = _flight_html(f'3d:["$",null,{{"document":{json.dumps(detail_payload)}}}]')
    document = parse_detail_page(
        detail_html,
        items[0],
        "https://jdihn.go.id/doc/42",
        ("pendidikan", "sekolah"),
        (),
    )
    assert document.attachment_url == "https://files.example.org/peraturan.pdf"
    assert document.law_number == "7"
    assert document.law_year == 2024
    assert document.matched_keywords == ("pendidikan", "sekolah")


def test_parse_search_fails_loudly() -> None:
    with pytest.raises(ParseError):
        parse_search_page("<html></html>")


def test_detail_exclude_keyword_wins() -> None:
    item = SearchItem(
        source_id="9",
        title="Peraturan Pendidikan Militer",
        document_number=None,
        category="peraturan",
        subtitle=None,
        description=None,
        institution=None,
        effective_status=None,
        law_type=None,
        law_year=None,
    )
    detail = {"id": 9, "canonicalTitle": item.title, "attachmentUrl": None}
    document = parse_detail_page(
        _flight_html(f'1:["$",{{"document":{json.dumps(detail)}}}]'),
        item,
        "https://jdihn.go.id/doc/9",
        ("pendidikan",),
        ("militer",),
    )
    assert document.matched_keywords == ()
