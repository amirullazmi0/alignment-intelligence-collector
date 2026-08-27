from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

LOGGER = logging.getLogger(__name__)


def asyncpg_dsn(database_url: str) -> str:
    """Buang parameter yang hanya dimengerti Prisma.

    Prisma menuliskan `?schema=public` di connection string, dan asyncpg menolaknya
    sebagai parameter yang tidak dikenal. Schema-nya sendiri tidak hilang: `public`
    memang sudah menjadi search_path bawaan Postgres.
    """
    parts = urlsplit(database_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "schema"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Database:
    """Pool asyncpg dengan umur hidup yang eksplisit.

    Dipakai bersama oleh API, scheduler, dan CLI. Prisma hanya memiliki schema-nya;
    seluruh query saat runtime ditulis sebagai SQL biasa di sini dan di `state.py`.
    """

    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=self._min_size, max_size=self._max_size
        )
        LOGGER.info("Pool database collector siap")

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
        LOGGER.info("Pool database collector ditutup")

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database belum terhubung; panggil connect() lebih dulu")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            yield connection

    async def __aenter__(self) -> Database:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


__all__ = ["Database", "asyncpg_dsn"]
