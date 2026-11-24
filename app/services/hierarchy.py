"""Cakupan dokumen yang dikumpulkan: hierarki peraturan dari Perda sampai UUD 1945.

Yang masuk hanya **peraturan** dalam hierarki perundang-undangan:

1. UUD 1945
2. TAP MPR
3. UU dan Perppu
4. Peraturan Pemerintah
5. Peraturan Presiden dan Peraturan Menteri
6. Perda Provinsi (termasuk Qanun Aceh)
7. Perda Kabupaten/Kota (termasuk Qanun kabupaten/kota)

Yang sengaja dibuang: semua bentuk *Keputusan* (Keppres, Kepmen) karena kebanyakan
berupa penetapan sekali pakai; *Instruksi* dan *Surat Edaran*; peraturan kepala daerah
(Pergub, Perbup, Perwali); peraturan badan/lembaga (BPJS, OJK, BI, MA, dll.); peraturan
rektor; dan putusan pengadilan. Aturan ini sama untuk JDIHN dan pasal.id.

Jenis dokumen dibaca dari **awal judul** lebih dulu, karena label jenis dari sumber bisa
keliru (di pasal.id ada Permenkumham berlabel PP). Kalau judul tidak menyebut jenisnya,
misalnya judul JDIHN "Standar Pendidikan Guru", baru `law_type` yang dipakai.
"""

from __future__ import annotations

import re

from ..clients.parsing import normalize_text
from ..domain.models import SearchItem
from .ministry import matches_ministry

#: Penanda bentuk yang dikenali tetapi berada di luar cakupan.
EXCLUDED = -1

# Urutan penting: pola yang lebih panjang harus dicoba sebelum awalannya.
# "peraturan pemerintah pengganti undang undang" sebelum "peraturan pemerintah", dan
# "undang undang dasar" sebelum "undang undang". Pola yang dibuang dicoba paling awal.
_FORMS: tuple[tuple[str, int], ...] = (
    (r"(?:keputusan|kepmen\w*|keppres|instruksi|inpres|surat edaran|se)\b", EXCLUDED),
    (
        r"(?:peraturan (?:gubernur|bupati|wali ?kota|kepala daerah|rektor|badan|lembaga|"
        r"kepala|bank indonesia|otoritas|mahkamah|komisi|dewan|direktur|sekretaris)|"
        r"pergub|perbup|perwal\w*|perka\w*|pojk|pbi|perma|sema|putusan)\b",
        EXCLUDED,
    ),
    (r"(?:undang undang dasar|uud)\b", 1),
    (r"(?:ketetapan|tap) mpr\w*\b", 2),
    (r"(?:peraturan pemerintah pengganti undang undang|perppu|perpu)\b", 3),
    (r"(?:undang undang|uu)\b", 3),
    (r"(?:peraturan pemerintah|pp)\b", 4),
    (r"(?:peraturan presiden|perpres)\b", 5),
    (r"(?:peraturan menteri|peraturan kementerian|permen\w*|pmk)\b", 5),
    (r"(?:peraturan daerah|perda|qanun) (?:provinsi|prov)\b", 6),
    (r"(?:peraturan daerah|perda|qanun) (?:kabupaten|kab|kota)\b", 7),
    (r"(?:peraturan daerah|perda|qanun)\b", 0),  # 0: tingkatnya dari instansi
)
_COMPILED = tuple((re.compile(rf"^{pattern}"), rank) for pattern, rank in _FORMS)


def _form_rank(text: str | None) -> int | None:
    """Rank bentuk di awal `text`, EXCLUDED, 0 untuk Perda tanpa tingkat, atau None."""
    normalized = normalize_text(text or "")
    if not normalized:
        return None
    for pattern, rank in _COMPILED:
        if pattern.match(normalized):
            return rank
    return None


def _regional_rank(item: SearchItem) -> int | None:
    """Perda tanpa tingkat di judul: provinsi atau kabupaten/kota dari instansinya."""
    # Instansi dulu: judul Perda provinsi bisa saja menyebut "kota" di bagian tentangnya.
    for text in (normalize_text(item.institution or ""), normalize_text(item.title)):
        if re.search(r"\b(?:kabupaten|kota|pemkab|pemkot)\b", text):
            return 7
        if re.search(r"\b(?:provinsi|pemprov)\b", text) or text.startswith("qanun aceh"):
            return 6
    return None


def title_states_form(item: SearchItem) -> bool:
    """Apakah judulnya sendiri menyebut bentuk peraturan yang dikenali.

    Dipakai untuk pasal.id, yang judulnya selalu diawali bentuknya. Judul yang tidak
    (mis. "PENDIDIKAN PANCASILA DAN ...") tanda metadata yang rusak, dan dibuang.
    """
    return _form_rank(item.title) is not None


def legal_hierarchy_rank(item: SearchItem) -> int | None:
    """Rank 1-7 kalau dokumen masuk cakupan, None kalau tidak."""
    rank = _form_rank(item.title)
    if rank is None:
        rank = _form_rank(item.law_type)
    if rank is None or rank == EXCLUDED:
        return None
    if rank == 0:
        return _regional_rank(item)
    return rank


# Nilai enum `LegalHierarchy` di prisma/schema.prisma, dipetakan dari rank hierarki.
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
    user memilih kementerian untuk mendapatkan dokumen kementerian itu. Kandidat di luar
    cakupan dibuang; sisanya tetap ikut di belakang antrean sampai batas
    MAX_UPLOADS_PER_RUN tercapai.
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
    "EXCLUDED",
    "LEGAL_HIERARCHY_LABELS",
    "legal_hierarchy_label",
    "legal_hierarchy_rank",
    "prioritize_legal_hierarchy",
]
