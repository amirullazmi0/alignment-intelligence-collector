from __future__ import annotations

import httpx
import pytest

from app.services.auth import (
    AlignmentAuthClient,
    AuthUnavailable,
    InvalidCredentials,
    NotAdmin,
)

BASE = "https://alignment.example.org"


def _client(handler: object) -> AlignmentAuthClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return AlignmentAuthClient(BASE, 5.0, client=httpx.AsyncClient(transport=transport))


def _session_body(role: str) -> dict[str, object]:
    """Bentuk balasan Nest yang sebenarnya: payload terbungkus `data`."""
    return {
        "data": {
            "user": {"id": "user-1", "email": "admin@example.org", "role": role},
            "permissions": [],
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
        }
    }


@pytest.mark.asyncio
async def test_login_membuka_amplop_data_milik_nest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/auth/login"
        return httpx.Response(200, json=_session_body("ADMIN"))

    session = await _client(handler).login("admin@example.org", "rahasia")

    assert session.access_token == "access-token"
    assert session.refresh_token == "refresh-token"
    assert session.user.email == "admin@example.org"


@pytest.mark.asyncio
async def test_login_menolak_akun_non_admin() -> None:
    """Kredensial benar tetapi bukan admin tetap ditolak, dan bukan sebagai 401."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_session_body("USER"))

    with pytest.raises(NotAdmin) as error:
        await _client(handler).login("staf@example.org", "rahasia")

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_login_meneruskan_pesan_galat_dari_nest() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Email atau password salah"})

    with pytest.raises(InvalidCredentials) as error:
        await _client(handler).login("admin@example.org", "salah")

    assert error.value.message == "Email atau password salah"


@pytest.mark.asyncio
async def test_login_membedakan_server_mati_dari_kredensial_salah() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(AuthUnavailable) as error:
        await _client(handler).login("admin@example.org", "rahasia")

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_me_menerima_profil_terbungkus_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            json={"data": {"user": {"id": "user-1", "email": "a@b.c", "role": "ADMIN"}}},
        )

    user = await _client(handler).me("access-token")

    assert user.id == "user-1"
    assert user.role == "ADMIN"


@pytest.mark.asyncio
async def test_me_menolak_sesi_yang_rolenya_turun_dari_admin() -> None:
    """Role bisa dicabut setelah login, jadi tiap pemeriksaan sesi mengecek ulang."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"user": {"id": "user-1", "email": "a@b.c", "role": "USER"}}},
        )

    with pytest.raises(NotAdmin):
        await _client(handler).me("access-token")


@pytest.mark.asyncio
async def test_refresh_mengembalikan_sesi_baru() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_session_body("ADMIN"))

    session = await _client(handler).refresh("refresh-token")

    assert session.access_token == "access-token"


@pytest.mark.asyncio
async def test_jaringan_mati_menjadi_auth_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(AuthUnavailable):
        await _client(handler).login("admin@example.org", "rahasia")
