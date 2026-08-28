"""Endpoint auth untuk dashboard kolektor.

Dashboard hanya berbicara dengan kolektor, jadi alamat dan kontrak Alignment Intelligence
tidak perlu diketahui frontend sama sekali. Route ini tetap berada di balik
`x-internal-token`: yang boleh memanggilnya adalah server dashboard, bukan browser.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ...config import CollectorSettings
from ...services.auth import AlignmentAuthClient, AuthError
from ..dependencies import get_settings
from ..middleware import Guarded

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginInput(BaseModel):
    # Sengaja bukan EmailStr: bentuk email divalidasi Nest, dan menambah
    # email-validator hanya untuk itu berarti satu dependensi lagi untuk dijaga.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)


class RefreshInput(BaseModel):
    refreshToken: str = Field(min_length=1)


def get_auth_client(
    settings: Annotated[CollectorSettings, Depends(get_settings)],
) -> AlignmentAuthClient:
    return AlignmentAuthClient(
        settings.require_alignment_api_base_url(), settings.http_timeout_seconds
    )


AuthClient = Annotated[AlignmentAuthClient, Depends(get_auth_client)]


def _bearer(authorization: str | None) -> str:
    """Ambil token dari header Authorization, atau tolak dengan 401."""
    prefix = "bearer "
    if not authorization or not authorization.lower().startswith(prefix):
        raise HTTPException(401, "Header Authorization tidak ada atau bukan Bearer")
    token = authorization[len(prefix) :].strip()
    if not token:
        raise HTTPException(401, "Token kosong")
    return token


@router.post("/login")
async def login(_: Guarded, body: LoginInput, client: AuthClient) -> dict[str, Any]:
    """Tukar kredensial Alignment dengan sesi. Hanya akun ADMIN yang diterima."""
    try:
        session = await client.login(body.email, body.password)
    except AuthError as error:
        raise HTTPException(error.status_code, error.message) from error
    finally:
        await client.aclose()
    return session.as_dict()


@router.get("/me")
async def me(
    _: Guarded,
    client: AuthClient,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        user = await client.me(_bearer(authorization))
    except AuthError as error:
        raise HTTPException(error.status_code, error.message) from error
    finally:
        await client.aclose()
    return {"user": user.as_dict()}


@router.post("/refresh")
async def refresh(_: Guarded, body: RefreshInput, client: AuthClient) -> dict[str, Any]:
    try:
        session = await client.refresh(body.refreshToken)
    except AuthError as error:
        raise HTTPException(error.status_code, error.message) from error
    finally:
        await client.aclose()
    return session.as_dict()


@router.post("/logout", status_code=204)
async def logout(
    _: Guarded,
    client: AuthClient,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    # Logout tidak pernah gagal dari sudut pandang dashboard: cookie dibuang apa pun
    # jawaban Nest, jadi header yang tidak sah pun cukup dijawab 204.
    try:
        if authorization:
            await client.logout(_bearer(authorization))
    except HTTPException:
        return
    finally:
        await client.aclose()
