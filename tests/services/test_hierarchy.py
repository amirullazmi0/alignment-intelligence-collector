from __future__ import annotations

import pytest

from app.domain.models import SearchItem
from app.services.hierarchy import (
    legal_hierarchy_label,
    legal_hierarchy_rank,
    prioritize_legal_hierarchy,
)


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
            7,
        ),
        (_item("Peraturan Bupati Jembrana Nomor 9"), 7),
        (_item("Perda Kabupaten tentang Usaha"), 7),
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


def _ministry_item(source_id: str, title: str, institution: str, law_type: str) -> SearchItem:
    return _item(title, source_id=source_id, institution=institution, law_type=law_type)


def test_ministry_documents_lead_without_dropping_the_rest() -> None:
    """Filter longgar: dokumen kementerian didahulukan, sisanya tetap ikut."""
    milik_kementerian = _ministry_item(
        "10", "Standar Pelayanan", "JDIH Kementerian Pariwisata", "PERATURAN KEMENTERIAN"
    )
    undang_undang_lain = _ministry_item(
        "20", "Undang-Undang Kepariwisataan", "JDIH Kementerian Hukum", "UNDANG-UNDANG"
    )
    permen_lain = _ministry_item(
        "30", "Peraturan Menteri Lain", "JDIH Kementerian Sosial", "PERATURAN KEMENTERIAN"
    )
    items = [undang_undang_lain, permen_lain, milik_kementerian]

    ordered = prioritize_legal_hierarchy(items, ministry="Kementerian Pariwisata")

    assert [item.source_id for item in ordered] == ["10", "20", "30"]
    assert len(ordered) == len(items), "tidak ada kandidat yang boleh dibuang"


def test_ordering_is_unchanged_when_no_ministry_is_selected() -> None:
    """Regressi: tanpa ministry, urutannya persis seperti sebelum fitur ini ada."""
    items = [
        _ministry_item(
            "30", "Peraturan Menteri", "JDIH Kementerian Sosial", "PERATURAN KEMENTERIAN"
        ),
        _ministry_item("20", "Undang-Undang Umum", "JDIH Kementerian Hukum", "UNDANG-UNDANG"),
    ]

    assert [item.source_id for item in prioritize_legal_hierarchy(items)] == ["20", "30"]


def test_ministry_priority_outranks_legal_hierarchy() -> None:
    """Keputusan desain yang disengaja dan perlu terlihat di test.

    User memilih kementerian justru untuk mendapatkan dokumen kementerian itu, jadi
    Peraturan Menteri miliknya harus mengalahkan Undang-Undang milik instansi lain saat
    MAX_UPLOADS_PER_RUN memotong antrean.
    """
    permen_terpilih = _ministry_item(
        "10", "Peraturan Menteri Pariwisata", "JDIH Kementerian Pariwisata", "PERATURAN KEMENTERIAN"
    )
    undang_undang_lain = _ministry_item(
        "20", "Undang-Undang Lain", "JDIH Kementerian Hukum", "UNDANG-UNDANG"
    )

    ordered = prioritize_legal_hierarchy(
        [undang_undang_lain, permen_terpilih], ministry="Kementerian Pariwisata"
    )

    assert ordered[0].source_id == "10"


@pytest.mark.parametrize(
    ("item", "expected_label"),
    [
        (_item("Undang-Undang Dasar 1945"), "UUD_1945"),
        (_item("TAP MPR tentang Usaha"), "TAP_MPR"),
        (_item("Undang-Undang tentang Perdagangan"), "UU_PERPU"),
        (_item("Peraturan Pemerintah tentang Usaha"), "PERATURAN_PEMERINTAH"),
        (_item("Peraturan Menteri Perdagangan"), "PERPRES_KEPPRES_PERMEN"),
        (
            _item(
                "Peraturan Daerah Provinsi tentang Usaha",
                institution="JDIH Pemerintah Provinsi Bali",
            ),
            "PERDA_PROVINSI",
        ),
        (
            _item("Peraturan Bupati tentang Usaha", institution="JDIH Kabupaten Jembrana"),
            "PERDA_KABUPATEN_KOTA",
        ),
        # Peraturan perguruan tinggi bukan bagian hierarki perundang-undangan.
        (_item("Peraturan Rektor Kurikulum", law_type="PERATURAN PERGURUAN TINGGI"), None),
    ],
)
def test_legal_hierarchy_label_matches_nest_enum(
    item: SearchItem, expected_label: str | None
) -> None:
    assert legal_hierarchy_label(item) == expected_label


def test_every_collected_rank_has_a_label() -> None:
    """Setiap rank yang bisa dihasilkan collector wajib punya padanan enum di Prisma."""
    from app.services.hierarchy import LEGAL_HIERARCHY_LABELS

    assert set(LEGAL_HIERARCHY_LABELS) == {1, 2, 3, 4, 5, 6, 7}


def test_regency_documents_sort_after_national_ones() -> None:
    """Ketujuh tingkat ikut, tapi Perda Kabupaten/Kota tetap di ekor antrean.

    JDIHN memuat puluhan ribu peraturan bupati hasil impor massal. Urutan inilah yang
    menjaga agar dokumen tingkat nasional tidak terdorong keluar oleh batas run.
    """
    undang_undang = _item("Undang-Undang tentang Usaha", source_id="10")
    permen = _item("Peraturan Menteri Perdagangan", source_id="20")
    perbup = _item("Peraturan Bupati Jembrana", source_id="30")

    ordered = prioritize_legal_hierarchy([perbup, permen, undang_undang])

    assert [item.source_id for item in ordered] == ["10", "20", "30"]


def test_rector_regulations_stay_excluded() -> None:
    """Satu-satunya yang tetap dibuang setelah ketujuh tingkat diterima."""
    rektor = _item("Peraturan Rektor Kurikulum", law_type="PERATURAN PERGURUAN TINGGI")

    assert legal_hierarchy_rank(rektor) is None
    assert prioritize_legal_hierarchy([rektor]) == []
