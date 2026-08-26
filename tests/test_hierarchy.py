from __future__ import annotations

import pytest

from alignment_collector.hierarchy import legal_hierarchy_rank, prioritize_legal_hierarchy
from alignment_collector.models import SearchItem


def _item(
    title: str,
    *,
    source_id: str = "1",
    institution: str | None = None,
    law_type: str | None = None,
    law_year: int | None = 2025,
) -> SearchItem:
    return SearchItem(
        source_id=source_id,
        title=title,
        document_number=None,
        category="peraturan",
        subtitle=None,
        description=None,
        institution=institution,
        effective_status="Berlaku",
        law_type=law_type,
        law_year=law_year,
    )


@pytest.mark.parametrize(
    ("item", "expected_rank"),
    [
        (_item("Undang-Undang Dasar 1945"), 1),
        (_item("TAP MPR tentang Pendidikan"), 2),
        (_item("Undang-Undang tentang Pendidikan Tinggi"), 3),
        (_item("Perpu tentang Pendidikan"), 3),
        (_item("Peraturan Pemerintah tentang Pendidikan"), 4),
        (_item("Peraturan Presiden tentang Pendidikan"), 5),
        (_item("Keputusan Presiden tentang Pendidikan"), 5),
        (_item("Peraturan Menteri Pendidikan"), 5),
        (
            _item(
                "Peraturan Daerah tentang Pendidikan",
                institution="Pemerintah Provinsi Jawa Barat",
            ),
            6,
        ),
        (_item("Peraturan Rektor Kurikulum Universitas"), None),
        (
            _item(
                "Peraturan Daerah tentang Pendidikan",
                institution="Pemerintah Kota Malang",
            ),
            None,
        ),
    ],
)
def test_legal_hierarchy_rank(item: SearchItem, expected_rank: int | None) -> None:
    assert legal_hierarchy_rank(item) == expected_rank


def test_prioritize_national_before_provincial_and_newer_year_first() -> None:
    provincial = _item(
        "Peraturan Daerah Provinsi tentang Pendidikan",
        source_id="province",
        law_year=2025,
    )
    older_minister = _item(
        "Peraturan Menteri Pendidikan",
        source_id="minister-old",
        law_year=2020,
    )
    newer_minister = _item(
        "Peraturan Menteri Pendidikan",
        source_id="minister-new",
        law_year=2024,
    )

    prioritized = prioritize_legal_hierarchy([provincial, older_minister, newer_minister])

    assert [item.source_id for item in prioritized] == [
        "minister-new",
        "minister-old",
        "province",
    ]


@pytest.mark.parametrize(
    ("item", "expected_rank"),
    [
        # JDIHN menuliskan regulationType sebagai "PERATURAN KEMENTERIAN", bukan
        # "peraturan menteri", sehingga varian ini sempat terbuang seluruhnya.
        (_item("Standar Pendidikan Guru", law_type="PERATURAN KEMENTERIAN"), 5),
        (_item("Sertifikasi Dosen", law_type="KEPUTUSAN KEMENTERIAN"), 5),
        (_item("Data Pokok Pendidikan", law_type="Peraturan Kementerian"), 5),
        (_item("Tunjangan Guru", law_type="KEPUTUSAN MENTERI"), 5),
        # Bukan peraturan tingkat nasional, harus tetap dibuang.
        (
            _item(
                "Himpunan Peraturan Kepegawaian",
                law_type="PERATURAN LEMBAGA PEMERINTAH NON KEMENTERIAN",
            ),
            None,
        ),
        (_item("Izin Pendirian Satuan Pendidikan", law_type="SURAT EDARAN KEMENTERIAN"), None),
        (_item("Peraturan Rektor Kurikulum", law_type="PERATURAN PERGURUAN TINGGI"), None),
    ],
)
def test_ministry_regulation_vocabulary_from_jdihn(
    item: SearchItem, expected_rank: int | None
) -> None:
    assert legal_hierarchy_rank(item) == expected_rank
