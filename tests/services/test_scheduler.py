from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app.config import CollectorSettings
from app.domain.models import RunSummary
from app.services.runner import RunBroker, RunExecutor
from app.services.scheduler import build_trigger, next_fire_time, staggered_cron
from app.storage.database import asyncpg_dsn
from app.storage.repository import Run


def _settings() -> CollectorSettings:
    return CollectorSettings(
        _env_file=None,
        DATABASE_URL="postgresql://localhost/tidak-dipakai",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )


def _run(run_id: str, ministry: str = "Kementerian Pariwisata") -> Run:
    return Run(
        id=run_id,
        schedule_id=None,
        ministry=ministry,
        ministry_label=ministry,
        keywords=(ministry,),
        trigger="MANUAL",
        status="QUEUED",
        dry_run=True,
        document_limit=3,
        started_at=None,
        finished_at=None,
        summary=None,
        error_message=None,
        created_at=datetime.now(UTC),
    )


class FakeRepository:
    """Repository tiruan yang mencatat urutan pemanggilan."""

    def __init__(self, runs: dict[str, Run]) -> None:
        self._runs = runs
        self.events: list[str] = []
        self.logs: list[dict[str, object]] = []

    async def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def start_run(self, run_id: str) -> None:
        self.events.append(f"start:{run_id}")

    async def finish_run(self, run_id: str, summary: RunSummary) -> None:
        self.events.append(f"finish:{run_id}")

    async def fail_run(self, run_id: str, message: str) -> None:
        self.events.append(f"fail:{run_id}")

    async def append_logs(self, run_id: str, lines: list[dict[str, object]]) -> None:
        self.logs.extend(lines)


def test_cron_expression_is_validated_before_it_is_stored() -> None:
    """Ekspresi salah harus ditolak saat jadwal dibuat, bukan diam-diam gagal nanti."""
    assert build_trigger("0 2 * * *", "Asia/Jakarta") is not None

    with pytest.raises(ValueError):
        build_trigger("bukan cron", "Asia/Jakarta")

    with pytest.raises(ValueError):
        build_trigger("99 99 * * *", "Asia/Jakarta")


def test_next_fire_time_respects_the_configured_timezone() -> None:
    jakarta = next_fire_time("0 2 * * *", "Asia/Jakarta")
    london = next_fire_time("0 2 * * *", "Europe/London")

    assert jakarta is not None
    assert london is not None
    assert jakarta.utcoffset() != london.utcoffset()


@pytest.mark.asyncio
async def test_runs_are_executed_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dua jadwal yang jatuh bersamaan harus antre, bukan berjalan bersamaan.

    JDIHN mereset koneksi kalau diberondong, dan handler log dipasang pada logger paket
    yang dipakai bersama -- dua run sekaligus akan saling mencampur keluarannya.
    """
    concurrent = 0
    peak = 0
    order: list[str] = []

    async def fake_run(self: object, request: object) -> RunSummary:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return RunSummary(mode="backfill", dry_run=True)

    monkeypatch.setattr("app.services.runner.CollectorService.run", fake_run)

    runs = {"run-a": _run("run-a"), "run-b": _run("run-b", "Kementerian Sosial")}
    repository = FakeRepository(runs)
    executor = RunExecutor(_settings(), object(), repository, RunBroker())  # type: ignore[arg-type]
    await executor.start()
    try:
        await executor.enqueue("run-a")
        await executor.enqueue("run-b")
        for _ in range(200):
            if repository.events.count("finish:run-a") and repository.events.count("finish:run-b"):
                break
            await asyncio.sleep(0.01)
    finally:
        await executor.stop()

    assert peak == 1, "hanya boleh satu run berjalan pada satu waktu"
    order = [event for event in repository.events if event.startswith(("start", "finish"))]
    assert order == ["start:run-a", "finish:run-a", "start:run-b", "finish:run-b"]


@pytest.mark.asyncio
async def test_failed_run_is_recorded_and_queue_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Satu run yang gagal tidak boleh menghentikan antrean berikutnya."""
    calls: list[str] = []

    async def fake_run(self: object, request: object) -> RunSummary:
        calls.append("dipanggil")
        if len(calls) == 1:
            raise RuntimeError("JDIHN menolak koneksi")
        return RunSummary(mode="backfill", dry_run=True)

    monkeypatch.setattr("app.services.runner.CollectorService.run", fake_run)

    runs = {"run-a": _run("run-a"), "run-b": _run("run-b", "Kementerian Sosial")}
    repository = FakeRepository(runs)
    executor = RunExecutor(_settings(), object(), repository, RunBroker())  # type: ignore[arg-type]
    await executor.start()
    try:
        await executor.enqueue("run-a")
        await executor.enqueue("run-b")
        for _ in range(200):
            if "finish:run-b" in repository.events:
                break
            await asyncio.sleep(0.01)
    finally:
        await executor.stop()

    assert "fail:run-a" in repository.events
    assert "finish:run-b" in repository.events


@pytest.mark.asyncio
async def test_broker_stops_dropping_events_after_unsubscribe() -> None:
    broker = RunBroker()
    queue = broker.subscribe("run-a")

    broker.publish("run-a", {"type": "log", "message": "halo"})
    assert queue.get_nowait() == {"type": "log", "message": "halo"}

    broker.unsubscribe("run-a", queue)
    broker.publish("run-a", {"type": "log", "message": "diabaikan"})
    assert queue.empty()


def test_prisma_schema_parameter_is_stripped_for_asyncpg() -> None:
    """Prisma menulis ?schema=public; asyncpg menolaknya sebagai parameter tak dikenal."""
    dsn = asyncpg_dsn("postgresql://user:pass@localhost:5432/db_collector?schema=public")

    assert "schema=public" not in dsn
    assert dsn.startswith("postgresql://user:pass@localhost:5432/db_collector")


def test_other_query_parameters_survive() -> None:
    dsn = asyncpg_dsn("postgresql://localhost/db?schema=public&sslmode=require")

    assert "sslmode=require" in dsn
    assert "schema=public" not in dsn


def test_staggered_cron_spreads_ministries_across_the_window() -> None:
    """Seluruh kementerian tidak boleh menyala pada menit yang sama.

    Kalau semuanya memakai `*/10 * * * *`, 49 run masuk antrean serentak dan antrean
    serial langsung menumpuk. Penyebaran ini membuat bebannya datang bertahap.
    """
    assert staggered_cron(0, 10) == "0,10,20,30,40,50 * * * *"
    assert staggered_cron(1, 10) == "1,11,21,31,41,51 * * * *"
    assert staggered_cron(9, 10) == "9,19,29,39,49,59 * * * *"
    # Indeks melampaui jendela berputar kembali ke awal.
    assert staggered_cron(10, 10) == staggered_cron(0, 10)


def test_staggered_cron_stays_valid_for_apscheduler() -> None:
    from app.domain.ministries import MINISTRIES

    slots = {staggered_cron(index, 10) for index in range(len(MINISTRIES))}
    assert len(slots) == 10, "49 kementerian harus tersebar ke 10 slot menit"

    for expression in slots:
        assert build_trigger(expression, "Asia/Jakarta") is not None


def test_staggered_cron_handles_edge_windows() -> None:
    assert staggered_cron(0, 1) == ",".join(str(m) for m in range(60)) + " * * * *"
    assert staggered_cron(3, 60) == "3 * * * *"


def test_staggered_cron_supports_hourly_periods() -> None:
    """Periode satu jam ke atas disebar lewat menit, dengan kolom jam yang melangkah."""
    assert staggered_cron(0, 120) == "0 */2 * * *"
    assert staggered_cron(7, 120) == "7 */2 * * *"
    assert staggered_cron(60, 120) == "0 */2 * * *", "menit berputar setelah 60"
    assert staggered_cron(3, 60) == "3 * * * *"
    assert staggered_cron(5, 1440) == "5 0 * * *"


def test_two_hourly_schedule_spreads_every_ministry_to_its_own_minute() -> None:
    from app.domain.ministries import MINISTRIES

    expressions = [staggered_cron(index, 120) for index in range(len(MINISTRIES))]
    assert len(set(expressions)) == len(MINISTRIES), "tiap kementerian dapat menit sendiri"

    for expression in expressions:
        assert build_trigger(expression, "Asia/Jakarta") is not None


def test_two_hourly_fires_twelve_times_a_day() -> None:
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Jakarta")
    trigger = build_trigger(staggered_cron(0, 120), "Asia/Jakarta")
    moment = datetime(2026, 1, 1, 0, 0, tzinfo=tz)
    hours: list[int] = []
    for _ in range(12):
        moment = trigger.get_next_fire_time(None, moment)
        hours.append(moment.hour)
        moment += timedelta(seconds=1)

    assert hours == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]
