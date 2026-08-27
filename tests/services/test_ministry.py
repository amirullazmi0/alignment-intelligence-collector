from __future__ import annotations

import pytest

from app.domain.models import SearchItem
from app.services.ministry import matches_ministry


def _item(institution: str | None) -> SearchItem:
    return SearchItem(
        source_id="1",
        title="Peraturan",
        document_number=None,
        category="peraturan",
        subtitle=None,
        description=None,
        institution=institution,
        effective_status="Berlaku",
        law_type="PERATURAN KEMENTERIAN",
        law_year=2025,
    )


@pytest.mark.parametrize(
    ("institution", "ministry", "expected"),
    [
        # JDIHN selalu memberi awalan "JDIH", jadi pencocokan harus substring.
        ("JDIH Kementerian Pariwisata", "Kementerian Pariwisata", True),
        ("JDIH Kementerian Perdagangan", "Kementerian Pariwisata", False),
        ("JDIH Pemerintah Kabupaten Jembrana", "Kementerian Pariwisata", False),
        # Nama tersimpan boleh lebih pendek daripada milik JDIHN.
        ("JDIH Kementerian Hukum dan Hak Asasi Manusia", "Kementerian Hukum", True),
        # Normalisasi menangani beda kapital, tanda baca, dan spasi ganda.
        ("JDIH  KEMENTERIAN   PARIWISATA", "kementerian pariwisata", True),
        ("JDIH Kementerian Pendidikan, Kebudayaan", "Kementerian Pendidikan Kebudayaan", True),
        (None, "Kementerian Pariwisata", False),
        ("", "Kementerian Pariwisata", False),
    ],
)
def test_ministry_matching(institution: str | None, ministry: str, expected: bool) -> None:
    assert matches_ministry(_item(institution), ministry) is expected


def test_alias_covers_pre_split_ministry_name() -> None:
    """Nomenklatur kementerian berubah tiap kabinet; alias yang menjembataninya."""
    item = _item("JDIH Kementerian Pariwisata dan Ekonomi Kreatif")

    assert matches_ministry(item, "Kementerian Pariwisata") is True
    assert matches_ministry(item, "Kementerian Ekonomi Kreatif") is False
    assert (
        matches_ministry(
            item, "Kementerian Ekonomi Kreatif", ("Kementerian Pariwisata dan Ekonomi Kreatif",)
        )
        is True
    )


def test_blank_ministry_never_matches_everything() -> None:
    """String kosong tidak boleh jadi substring yang cocok dengan semua instansi."""
    assert matches_ministry(_item("JDIH Kementerian Sosial"), "") is False
    assert matches_ministry(_item("JDIH Kementerian Sosial"), "   ") is False
