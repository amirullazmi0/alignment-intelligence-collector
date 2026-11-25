from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from ..services.catalog_sync import CatalogSync
from ..services.runner import RunBroker, RunExecutor
from ..services.scheduler import CollectorScheduler
from ..storage.catalog import CatalogStore
from ..storage.database import Database
from ..storage.repository import CollectorRepository
from ..storage.state import StateStore
from .dependencies import get_settings
from .routes import api_router

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Rakit komponen berumur panjang, lalu bongkar rapi saat service berhenti.

    Semuanya dipasang ke `app.state` dan hanya diambil lewat `api/dependencies.py`,
    sehingga route tidak pernah merakit apa pun sendiri.
    """
    settings = get_settings()
    database = Database(settings.require_database_url())
    await database.connect()

    state = StateStore(database)
    await state.verify_schema()

    repository = CollectorRepository(database)
    broker = RunBroker()
    executor = RunExecutor(settings, state, repository, broker)
    scheduler = CollectorScheduler(repository, executor)

    await executor.start()
    # Sebelum cron mulai menambah pekerjaan baru, samakan dulu keadaan database
    # dengan antrean yang baru saja kosong: run yang terputus restart ditandai
    # gagal, dan run yang belum sempat diambil dimuat ulang.
    await executor.recover()
    await scheduler.start()

    catalog = CatalogStore(database)
    catalog_sync = CatalogSync(settings, catalog, state)
    background: set[asyncio.Task[Any]] = set()
    if settings.pgo_sync_enabled:
        scheduler.register_catalog_jobs(catalog_sync)
        if await catalog.count() == 0:
            # First start: mirror the whole catalogue now instead of waiting for the night.
            task = asyncio.create_task(catalog_sync.run("sweep"))
            background.add(task)
            task.add_done_callback(background.discard)

    app.state.database = database
    app.state.state_store = state
    app.state.repository = repository
    app.state.broker = broker
    app.state.executor = executor
    app.state.scheduler = scheduler
    app.state.catalog = catalog
    app.state.catalog_sync = catalog_sync
    app.state.background_tasks = background
    try:
        yield
    finally:
        await scheduler.stop()
        for task in background:
            task.cancel()
        await executor.stop()
        await database.close()


def create_app() -> FastAPI:
    application = FastAPI(title="JDIHN Collector", version="1.0.0", lifespan=lifespan)
    application.include_router(api_router)
    return application


#: Dipakai uvicorn lewat `app.api:app`.
app = create_app()
