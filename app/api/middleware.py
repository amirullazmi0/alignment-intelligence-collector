from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from .dependencies import get_settings


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """Tolak permintaan tanpa token internal yang benar.

    Perbandingannya memakai `compare_digest` supaya lama pemeriksaan tidak bergantung
    pada seberapa banyak karakter awal yang cocok.

    Token yang kosong berarti pemeriksaan dilewati. Itu memudahkan pengembangan lokal,
    dan konsekuensinya harus disadari saat deploy: tanpa token, siapa pun yang bisa
    menjangkau porta ini bisa memicu run.
    """
    expected = get_settings().collector_internal_token.get_secret_value()
    if not expected:
        return
    if not secrets.compare_digest(x_internal_token or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "x-internal-token tidak valid")


#: Dipasang pada route yang hanya boleh dipanggil dashboard, bukan publik.
Guarded = Annotated[None, Depends(require_internal_token)]

__all__ = ["Guarded", "require_internal_token"]
