"""Autentikasi dashboard kolektor terhadap Alignment Intelligence.

Kolektor tidak punya tabel pengguna sendiri. Kredensial diteruskan ke `/v1/auth/login`
milik Nest, yang memang pemilik tabel `users` beserta hash kata sandinya. Menyalin logika
bcrypt dan JWT ke sini berarti dua tempat yang harus dijaga tetap sama, ditambah satu
kredensial database lagi yang beredar.

Dashboard ini bisa memicu pengumpulan dan pengunggahan dokumen ke pustaka bersama, jadi
hanya akun ber-role ADMIN yang diterima. Pemeriksaan itu dilakukan di sini, bukan di
frontend, supaya tidak ada jalur yang bisa melewatinya.
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any

import httpx
import truststore

LOGGER = logging.getLogger(__name__)

ADMIN_ROLE = "ADMIN"


class AuthError(RuntimeError):
    """Kegagalan autentikasi yang sudah punya pesan layak tampil ke pengguna."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InvalidCredentials(AuthError):
    def __init__(self, message: str = "Email atau kata sandi salah.") -> None:
        super().__init__(message, status_code=401)


class NotAdmin(AuthError):
    def __init__(self) -> None:
        super().__init__(
            "Akun ini bukan admin. Dashboard kolektor hanya untuk akun dengan role ADMIN.",
            status_code=403,
        )


class AuthUnavailable(AuthError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Server autentikasi Alignment tidak dapat dihubungi. {detail}",
            status_code=503,
        )


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: str
    email: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "email": self.email, "role": self.role}


@dataclass(frozen=True, slots=True)
class AuthSession:
    user: AuthUser
    access_token: str
    refresh_token: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "user": self.user.as_dict(),
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
        }


def _unwrap(payload: object) -> dict[str, Any]:
    """Buka amplop `{"data": ...}` yang dipasang ResponseInterceptor milik Nest."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data")
    if isinstance(inner, dict):
        return inner
    return payload


def _message(response: httpx.Response, fallback: str) -> str:
    try:
        body: object = response.json()
    except ValueError:
        return fallback
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str) and message:
            return message
        if isinstance(message, list):
            joined = "; ".join(item for item in message if isinstance(item, str))
            if joined:
                return joined
    return fallback


def _read_user(payload: dict[str, Any]) -> AuthUser | None:
    """Nest mengirim profil kadang datar, kadang terbungkus `user`."""
    source = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    if not isinstance(source, dict):
        return None
    identifier = source.get("id")
    email = source.get("email")
    role = source.get("role")
    if not isinstance(identifier, str) or not isinstance(email, str):
        return None
    return AuthUser(id=identifier, email=email, role=role if isinstance(role, str) else "")


def _read_session(payload: dict[str, Any]) -> AuthSession | None:
    user = _read_user(payload)
    access = payload.get("accessToken")
    refresh = payload.get("refreshToken")
    if user is None or not isinstance(access, str) or not isinstance(refresh, str):
        return None
    return AuthSession(user=user, access_token=access, refresh_token=refresh)


class AlignmentAuthClient:
    """Klien tipis untuk endpoint auth Nest.

    Sengaja terpisah dari `AlignmentClient`, yang memegang akun layanan kolektor untuk
    mengunggah dokumen. Di sini kredensialnya milik pengguna dashboard dan tidak pernah
    disimpan.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        if client is None:
            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._client = httpx.AsyncClient(timeout=timeout_seconds, verify=ssl_context)
        else:
            self._client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AlignmentAuthClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, f"{self._base_url}{path}", **kwargs)
        except httpx.HTTPError as error:
            raise AuthUnavailable(str(error)) from error

    async def login(self, email: str, password: str) -> AuthSession:
        response = await self._request(
            "POST", "/v1/auth/login", json={"email": email, "password": password}
        )
        if response.status_code == 401:
            raise InvalidCredentials(_message(response, "Email atau kata sandi salah."))
        if response.status_code >= 500:
            raise AuthUnavailable(_message(response, f"HTTP {response.status_code}"))
        if response.status_code >= 400:
            raise InvalidCredentials(
                _message(response, f"Login gagal (HTTP {response.status_code}).")
            )

        session = _read_session(_unwrap(response.json()))
        if session is None:
            raise AuthUnavailable("Balasan login tidak dikenali.")
        if session.user.role != ADMIN_ROLE:
            # Ditolak setelah kredensialnya benar: log ini menjelaskan penolakan yang
            # kalau tidak akan terbaca sebagai kata sandi salah.
            LOGGER.warning(
                "Login dashboard ditolak, role bukan admin: email=%s role=%s",
                session.user.email,
                session.user.role or "(kosong)",
            )
            raise NotAdmin()
        return session

    async def me(self, access_token: str) -> AuthUser:
        response = await self._request(
            "GET", "/v1/auth/me", headers={"authorization": f"Bearer {access_token}"}
        )
        if response.status_code == 401:
            raise InvalidCredentials("Sesi sudah berakhir.")
        if response.status_code >= 400:
            raise AuthUnavailable(_message(response, f"HTTP {response.status_code}"))

        user = _read_user(_unwrap(response.json()))
        if user is None:
            raise AuthUnavailable("Balasan profil tidak dikenali.")
        if user.role != ADMIN_ROLE:
            raise NotAdmin()
        return user

    async def refresh(self, refresh_token: str) -> AuthSession:
        response = await self._request(
            "POST", "/v1/auth/refresh", json={"refreshToken": refresh_token}
        )
        if response.status_code == 401:
            raise InvalidCredentials("Sesi sudah berakhir.")
        if response.status_code >= 400:
            raise AuthUnavailable(_message(response, f"HTTP {response.status_code}"))

        session = _read_session(_unwrap(response.json()))
        if session is None:
            raise AuthUnavailable("Balasan refresh tidak dikenali.")
        if session.user.role != ADMIN_ROLE:
            raise NotAdmin()
        return session

    async def logout(self, access_token: str) -> None:
        """Cabut sesi di sisi Nest; kegagalan tidak boleh menahan logout lokal."""
        try:
            await self._request(
                "POST", "/v1/auth/logout", headers={"authorization": f"Bearer {access_token}"}
            )
        except AuthError:
            LOGGER.info("Logout di Nest gagal; sesi lokal tetap dibuang")


__all__ = [
    "ADMIN_ROLE",
    "AlignmentAuthClient",
    "AuthError",
    "AuthSession",
    "AuthUnavailable",
    "AuthUser",
    "InvalidCredentials",
    "NotAdmin",
]
