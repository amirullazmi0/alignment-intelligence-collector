from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..services.runner import RunBroker, RunExecutor
from ..services.scheduler import CollectorScheduler
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
    await scheduler.start()

    app.state.database = database
    app.state.state_store = state
    app.state.repository = repository
    app.state.broker = broker
    app.state.executor = executor
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        await scheduler.stop()
        await executor.stop()
        await database.close()


def create_app() -> FastAPI:
    application = FastAPI(title="JDIHN Collector", version="1.0.0", lifespan=lifespan)
    application.include_router(api_router)
    return application


#: Dipakai uvicorn lewat `app.api:app`.
app = create_app()
