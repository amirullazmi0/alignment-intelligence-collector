from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import CollectorSettings
from app.services.runner import INTERRUPTED_MESSAGE, RunBroker, RunExecutor


class FakeRepository:
    """Cukup mewakili dua panggilan yang dipakai pemulihan antrean."""

    def __init__(self, interrupted: int, pending: list[str]) -> None:
        self._interrupted = interrupted
        self._pending = pending
        self.recover_message: str | None = None

    async def recover_interrupted_runs(self, message: str) -> int:
        self.recover_message = message
        return self._interrupted

    async def pending_run_ids(self) -> list[str]:
        return list(self._pending)


def _settings() -> CollectorSettings:
    return CollectorSettings(ALIGNMENT_PASSWORD=SecretStr("private-value"))


def _executor(repository: FakeRepository) -> RunExecutor:
    return RunExecutor(_settings(), state=None, repository=repository, broker=RunBroker())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_recover_requeues_pending_runs() -> None:
    """Run QUEUED yang ditinggalkan restart harus kembali masuk antrean memori."""
    repository = FakeRepository(interrupted=0, pending=["run-a", "run-b"])
    executor = _executor(repository)

    interrupted, requeued = await executor.recover()

    assert (interrupted, requeued) == (0, 2)
    assert executor.queue_length == 2


@pytest.mark.asyncio
async def test_recover_fails_runs_left_running() -> None:
    """Run yang terputus tidak bisa dilanjutkan, jadi ia ditandai gagal dengan alasannya."""
    repository = FakeRepository(interrupted=3, pending=[])
    executor = _executor(repository)

    interrupted, requeued = await executor.recover()

    assert (interrupted, requeued) == (3, 0)
    assert repository.recover_message == INTERRUPTED_MESSAGE
    assert executor.queue_length == 0


@pytest.mark.asyncio
async def test_recover_is_quiet_when_nothing_was_left_behind() -> None:
    repository = FakeRepository(interrupted=0, pending=[])
    executor = _executor(repository)

    assert await executor.recover() == (0, 0)
    assert executor.queue_length == 0
