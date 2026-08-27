from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from ..config import CollectorSettings
from ..services.runner import RunBroker, RunExecutor
from ..services.scheduler import CollectorScheduler
from ..storage.repository import CollectorRepository
from ..storage.state import StateStore

# Komponen berumur panjang dipasang ke `app.state` saat lifespan dan diambil lewat
# fungsi-fungsi di bawah, sehingga route tidak pernah menyentuh state itu langsung.


@lru_cache(maxsize=1)
def get_settings() -> CollectorSettings:
    return CollectorSettings()


def get_repository(request: Request) -> CollectorRepository:
    repository: CollectorRepository = request.app.state.repository
    return repository


def get_scheduler(request: Request) -> CollectorScheduler:
    scheduler: CollectorScheduler = request.app.state.scheduler
    return scheduler


def get_executor(request: Request) -> RunExecutor:
    executor: RunExecutor = request.app.state.executor
    return executor


def get_broker(request: Request) -> RunBroker:
    broker: RunBroker = request.app.state.broker
    return broker


def get_state_store(request: Request) -> StateStore:
    store: StateStore = request.app.state.state_store
    return store


__all__ = [
    "get_broker",
    "get_executor",
    "get_repository",
    "get_scheduler",
    "get_settings",
    "get_state_store",
]
