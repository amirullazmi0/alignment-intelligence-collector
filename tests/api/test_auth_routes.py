from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.dependencies import get_settings
from app.api.middleware import require_internal_token
from app.api.routes import auth as auth_routes
from app.config import CollectorSettings
from app.services.auth import AlignmentAuthClient

BASE = "https://alignment.example.org"


def _session_body(role: str) -> dict[str, object]:
    return {
        "data": {
            "user": {"id": "user-1", "email": "admin@example.org", "role": role},
            "permissions": [],
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
        }
    }


def _app(handler: object) -> FastAPI:
    """App berisi route auth saja.

    Lifespan aplikasi sesungguhnya menyalakan scheduler dan mulai mengumpulkan dokumen,
    jadi ia sengaja tidak dipakai di sini: yang diuji adalah kontrak HTTP route-nya.
    """
    application = FastAPI()
    application.include_router(auth_routes.router)

    def override() -> Iterator[AlignmentAuthClient]:
        transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
        yield AlignmentAuthClient(BASE, 5.0, client=httpx.AsyncClient(transport=transport))

    application.dependency_overrides[auth_routes.get_auth_client] = override
    # Penjaga x-internal-token dilewati di sini supaya tes tidak bergantung pada isi
    # .env pengembang. Bahwa penjaga itu benar-benar terpasang diuji terpisah di
    # test_guard_menolak_tanpa_internal_token.
    application.dependency_overrides[require_internal_token] = lambda: None
    return application


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(handler)), base_url="http://collector"
    )


async def test_login_admin_mengembalikan_sesi() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_session_body("ADMIN"))

    async with _client(upstream) as client:
        response = await client.post(
            "/v1/auth/login", json={"email": "admin@example.org", "password": "rahasia"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"] == "access-token"
    assert body["user"]["role"] == "ADMIN"


async def test_login_non_admin_ditolak_403() -> None:
    """Kredensial benar tetapi bukan admin harus terbaca berbeda dari kata sandi salah."""

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_session_body("USER"))

    async with _client(upstream) as client:
        response = await client.post(
            "/v1/auth/login", json={"email": "staf@example.org", "password": "rahasia"}
        )

    assert response.status_code == 403
    assert "ADMIN" in response.json()["detail"]


async def test_login_kredensial_salah_401() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Email atau password salah"})

    async with _client(upstream) as client:
        response = await client.post(
            "/v1/auth/login", json={"email": "admin@example.org", "password": "salah"}
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Email atau password salah"


async def test_me_tanpa_header_ditolak() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:  # pragma: no cover - tidak terpanggil
        raise AssertionError("Nest tidak boleh dihubungi tanpa token")

    async with _client(upstream) as client:
        response = await client.get("/v1/auth/me")

    assert response.status_code == 401


async def test_me_mengembalikan_pengguna_admin() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            json={"data": {"user": {"id": "user-1", "email": "a@b.c", "role": "ADMIN"}}},
        )

    async with _client(upstream) as client:
        response = await client.get("/v1/auth/me", headers={"authorization": "Bearer access-token"})

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "a@b.c"


async def test_logout_selalu_204() -> None:
    """Logout tidak boleh gagal dari sisi dashboard; cookie tetap dibuang."""

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(upstream) as client:
        response = await client.post(
            "/v1/auth/logout", headers={"authorization": "Bearer access-token"}
        )

    assert response.status_code == 204


@pytest.mark.parametrize(
    "payload",
    [{"email": "a@b.c"}, {"password": "rahasia"}, {"email": "", "password": ""}],
)
async def test_login_menolak_body_tidak_lengkap(payload: dict[str, str]) -> None:
    def upstream(_: httpx.Request) -> httpx.Response:  # pragma: no cover - tidak terpanggil
        raise AssertionError("Body tidak sah tidak boleh sampai ke Nest")

    async with _client(upstream) as client:
        response = await client.post("/v1/auth/login", json=payload)

    assert response.status_code == 422


async def test_guard_menolak_tanpa_internal_token() -> None:
    """Endpoint auth ikut berada di balik x-internal-token, bukan terbuka untuk publik."""

    def upstream(_: httpx.Request) -> httpx.Response:  # pragma: no cover - tidak terpanggil
        raise AssertionError("Permintaan tanpa token tidak boleh diteruskan")

    application = _app(upstream)
    # Kembalikan penjaga aslinya, lalu paksa ada token yang diharapkan.
    application.dependency_overrides.pop(require_internal_token)
    application.dependency_overrides[get_settings] = lambda: CollectorSettings(
        COLLECTOR_INTERNAL_TOKEN=SecretStr("token-rahasia"),
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://collector"
    ) as client:
        tanpa = await client.post(
            "/v1/auth/login", json={"email": "admin@example.org", "password": "rahasia"}
        )
        salah = await client.post(
            "/v1/auth/login",
            json={"email": "admin@example.org", "password": "rahasia"},
            headers={"x-internal-token": "token-keliru"},
        )

    assert tanpa.status_code == 401
    assert salah.status_code == 401
