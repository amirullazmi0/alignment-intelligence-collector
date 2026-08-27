from __future__ import annotations

from ..clients.parsing import normalize_text
from ..domain.models import SearchItem


def matches_ministry(item: SearchItem, ministry: str, aliases: tuple[str, ...] = ()) -> bool:
    """Apakah `item` diterbitkan oleh kementerian yang dipilih user.

    JDIHN menuliskan pemilik dokumen pada field `instansi` dengan awalan "JDIH", misalnya
    `"JDIH Kementerian Pariwisata"`. Pencocokan sengaja memakai substring atas teks yang
    sudah dinormalisasi, bukan kesetaraan persis, karena dua alasan: awalan "JDIH" tidak
    perlu dibuang, dan nomenklatur kementerian ikut berubah tiap kabinet sehingga string
    tersimpan bisa lebih pendek atau lebih panjang daripada milik JDIHN.

    `aliases` menampung nama lama atau nama gabungan sebelum sebuah kementerian dipecah,
    misalnya "Kementerian Pariwisata dan Ekonomi Kreatif" untuk "Kementerian Pariwisata".
    """
    institution = normalize_text(item.institution or "")
    if not institution:
        return False
    candidates = (ministry, *aliases)
    return any(
        normalized in institution
        for normalized in (normalize_text(candidate) for candidate in candidates)
        if normalized
    )


__all__ = ["matches_ministry"]
