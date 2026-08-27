"""Route HTTP, dikelompokkan per sumber daya."""

from __future__ import annotations

from fastapi import APIRouter

from . import health, ministries, runs, scheduler, schedules

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(ministries.router)
api_router.include_router(schedules.router)
api_router.include_router(runs.router)
api_router.include_router(scheduler.router)

__all__ = ["api_router"]
