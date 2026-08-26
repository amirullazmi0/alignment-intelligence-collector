from __future__ import annotations

import re

from .models import SearchItem
from .parsing import normalize_text


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
    return 6 if is_provincial_regulation else None


def prioritize_legal_hierarchy(items: list[SearchItem]) -> list[SearchItem]:
    ranked = [(rank, item) for item in items if (rank := legal_hierarchy_rank(item)) is not None]
    ranked.sort(
        key=lambda value: (
            value[0],
            -(value[1].law_year or 0),
            value[1].title.casefold(),
            value[1].source_id,
        )
    )
    return [item for _, item in ranked]


__all__ = ["legal_hierarchy_rank", "prioritize_legal_hierarchy"]
