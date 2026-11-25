from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.storage.state import CURRENT_RUN_ID, StateStore


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *params: Any) -> int:
        self.calls.append((sql, params))
        return 1

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        now = datetime(2026, 11, 25, tzinfo=UTC)
        return [
            {
                "source_id": "pgo:uu-no-1-tahun-2026",
                "source_url": "https://peraturan.go.id/id/uu-no-1-tahun-2026",
                "title": "UU 1/2026",
                "attachment_url": None,
                "institution": None,
                "status": "UPLOADED",
                "target_document_id": "doc-1",
                "attempts": 1,
                "error_code": None,
                "error_message": None,
                "first_seen_at": now,
                "updated_at": now,
                "run_id": "11111111-1111-1111-1111-111111111111",
                "schedule_id": None,
                "ministry_label": "Semua kementerian",
            }
        ]

    async def execute(self, sql: str, *params: Any) -> None:
        self.calls.append((sql, params))


class FakeDatabase:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[RecordingConnection]:
        yield self.connection


def _placeholders(sql: str) -> set[int]:
    return {int(n) for n in re.findall(r"\$(\d+)", sql)}


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"status": "UPLOADED"},
        {"source": "JDIHN"},
        {"source": "PERATURAN_GO_ID", "search": "upah"},
        {
            "status": "FAILED_PERMANENT",
            "source": "PASAL_ID",
            "run_id": "11111111-1111-1111-1111-111111111111",
            "schedule_id": "22222222-2222-2222-2222-222222222222",
            "search": "pajak",
        },
    ],
)
async def test_every_placeholder_has_a_parameter(filters: dict[str, str]) -> None:
    database = FakeDatabase()
    store = StateStore(database)  # type: ignore[arg-type]
    items, total = await store.list_documents(page=2, limit=10, **filters)  # type: ignore[arg-type]

    count_sql, count_params = database.connection.calls[0]
    list_sql, list_params = database.connection.calls[1]
    assert _placeholders(count_sql) == set(range(1, len(count_params) + 1))
    assert _placeholders(list_sql) == set(range(1, len(list_params) + 1))
    assert list_params[-2:] == (10, 10)  # limit, offset of page 2
    assert total == 1
    assert items[0]["source"] == "PERATURAN_GO_ID"
    assert items[0]["runLabel"] == "Semua kementerian"


async def test_recorded_documents_carry_the_current_run() -> None:
    from app.domain.models import DocumentState, SourceDocument

    database = FakeDatabase()
    store = StateStore(database)  # type: ignore[arg-type]
    document = SourceDocument(
        source_id="42",
        source_url="https://jdihn.go.id/doc/42",
        title="Peraturan",
        attachment_url=None,
        category="peraturan",
        document_number=None,
        law_type=None,
        law_number=None,
        law_year=None,
        effective_status=None,
        matched_keywords=("x",),
        fingerprint="f",
    )
    token = CURRENT_RUN_ID.set("11111111-1111-1111-1111-111111111111")
    try:
        await store.record(document, DocumentState.UPLOADED)
    finally:
        CURRENT_RUN_ID.reset(token)
    sql, params = database.connection.calls[0]
    assert "last_run_id" in sql
    assert params[-1] == "11111111-1111-1111-1111-111111111111"
    assert _placeholders(sql) == set(range(1, len(params) + 1))
