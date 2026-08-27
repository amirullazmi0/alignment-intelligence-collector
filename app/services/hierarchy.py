from __future__ import annotations

import re

from ..clients.parsing import normalize_text
from ..domain.models import SearchItem
from .ministry import matches_ministry


def _contains(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


def legal_hierarchy_rank(item: SearchItem) -> int | None:
    text = normalize_text(item.searchable_text)
    institution = normalize_text(item.institution or "")

    if _contains(text, r"\b(?:peraturan|keputusan) rektor\b"):
        return None
    if _contains(text, r"\b(?:undang undang dasar|uud 1945)\b"):
        return 1
    if _contains(text, r"\b(?:ketetapan mpr|tap mpr)\b"):
        return 2
    if _contains(text, r"\b(?:peraturan pemerintah pengganti undang undang|perpu)\b"):
        return 3
    if _contains(text, r"\bundang undang\b"):
        return 3
    if _contains(text, r"\bperaturan pemerintah\b"):
        return 4
    if _contains(
        text,
        # JDIHN memakai "PERATURAN/KEPUTUSAN KEMENTERIAN" pada regulationType, bukan
        # "peraturan menteri", sehingga varian itu harus ikut dikenali. "PERATURAN
        # LEMBAGA PEMERINTAH NON KEMENTERIAN" tetap tidak cocok karena bukan frasa
        # yang berdampingan.
        r"\b(?:peraturan presiden|perpres|keputusan presiden|keppres|"
        r"peraturan menteri|keputusan menteri|permen\w*|kepmen\w*|"
        r"peraturan kementerian|keputusan kementerian)\b",
    ):
        return 5
    is_provincial_regulation = _contains(
        text, r"\b(?:peraturan daerah provinsi|perda provinsi)\b"
    ) or (
        _contains(text, r"\b(?:peraturan daerah|perda)\b")
        and _contains(institution, r"\b(?:provinsi|pemprov|dprd provinsi)\b")
    )
    if is_provincial_regulation:
        return 6

    is_regency_regulation = _contains(
        text,
        r"\b(?:peraturan daerah kabupaten|peraturan daerah kota|perda kabupaten|perda kota|"
        r"peraturan bupati|peraturan wali kota|peraturan walikota|perbup|perwali|perwal)\b",
    ) or (
        _contains(text, r"\b(?:peraturan daerah|perda)\b")
        and _contains(institution, r"\b(?:kabupaten|kota|pemkab|pemkot)\b")
    )
    return 7 if is_regency_regulation else None


# Nilai enum `LegalHierarchy` di prisma/schema.prisma, dipetakan dari rank hierarki.
# Ketujuh tingkat dikumpulkan; yang tetap dibuang hanya peraturan rektor, karena
# peraturan perguruan tinggi memang tidak berada dalam hierarki perundang-undangan.
LEGAL_HIERARCHY_LABELS: dict[int, str] = {
    1: "UUD_1945",
    2: "TAP_MPR",
    3: "UU_PERPU",
    4: "PERATURAN_PEMERINTAH",
    5: "PERPRES_KEPPRES_PERMEN",
    6: "PERDA_PROVINSI",
    7: "PERDA_KABUPATEN_KOTA",
}


def legal_hierarchy_label(item: SearchItem) -> str | None:
    """Tingkat hierarki dokumen dalam bentuk yang dimengerti Alignment API."""
    rank = legal_hierarchy_rank(item)
    return None if rank is None else LEGAL_HIERARCHY_LABELS.get(rank)


def prioritize_legal_hierarchy(
    items: list[SearchItem],
    *,
    ministry: str | None = None,
    aliases: tuple[str, ...] = (),
) -> list[SearchItem]:
    """Urutkan kandidat; dokumen milik kementerian terpilih didahulukan.

    Prioritas kementerian sengaja ditaruh di depan rank hierarki, bukan sesudahnya, karena
    user memilih kementerian untuk mendapatkan dokumen kementerian itu. Tidak ada kandidat
    yang dibuang: dokumen tingkat nasional lain tetap ikut di belakang antrean sampai
    batas MAX_UPLOADS_PER_RUN tercapai.
    """
    ranked = [(rank, item) for item in items if (rank := legal_hierarchy_rank(item)) is not None]
    ranked.sort(
        key=lambda value: (
            0 if ministry and matches_ministry(value[1], ministry, aliases) else 1,
            value[0],
            -(value[1].law_year or 0),
            value[1].title.casefold(),
            value[1].source_id,
        )
    )
    return [item for _, item in ranked]


__all__ = [
    "LEGAL_HIERARCHY_LABELS",
    "legal_hierarchy_label",
    "legal_hierarchy_rank",
    "prioritize_legal_hierarchy",
]
