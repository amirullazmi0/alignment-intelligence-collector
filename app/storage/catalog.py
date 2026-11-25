"""Mirror of the peraturan.go.id catalogue (tables `regulations`, `regulation_changes`).

Every regulation of the catalogue has one row, filled from the list pages and
refreshed from its detail page. The text of its articles lives in Alignment
(`alignment_document_id`); the PDF stays at peraturan.go.id.

Schema owned by Prisma (migration 0006); plain SQL here, like `state.py`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .database import Database

#: Order in which regulations get their text: national levels first.
CATALOGUE_PRIORITY = ("uu", "perppu", "pp", "perpres", "permen", "perda")
#: A regulation whose text failed this many times stops being retried.
MAX_TEXT_ATTEMPTS = 5
MAX_ERROR = 500

_PRIORITY_SQL = (
    "array_position(ARRAY["
    + ",".join(f"'{slug}'" for slug in CATALOGUE_PRIORITY)
    + "]::text[], catalogue)"
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ListedRegulation:
    source_id: str
    catalogue: str
    title: str
    law_type: str
    law_number: str | None
    law_year: int | None
    issuer: str | None
    page_url: str
    pdf_url: str | None


@dataclass(frozen=True, slots=True)
class Regulation:
    source_id: str
    catalogue: str
    title: str
    law_type: str
    law_number: str | None
    law_year: int | None
    issuer: str | None
    status: str | None
    relations: list[dict[str, str]]
    page_url: str
    pdf_url: str | None
    fingerprint: str | None
    text_status: str
    pdf_sha256: str | None
    alignment_document_id: str | None


def _regulation(row: Any) -> Regulation:
    relations = row["relations"]
    if isinstance(relations, str):
        relations = json.loads(relations)
    return Regulation(
        source_id=row["source_id"],
        catalogue=row["catalogue"],
        title=row["title"],
        law_type=row["law_type"],
        law_number=row["law_number"],
        law_year=row["law_year"],
        issuer=row["issuer"],
        status=row["status"],
        relations=list(relations or []),
        page_url=row["page_url"],
        pdf_url=row["pdf_url"],
        fingerprint=row["fingerprint"],
        text_status=row["text_status"],
        pdf_sha256=row["pdf_sha256"],
        alignment_document_id=row["alignment_document_id"],
    )


_COLUMNS = """source_id, catalogue, title, law_type, law_number, law_year, issuer, status,
    relations, page_url, pdf_url, fingerprint, text_status, pdf_sha256, alignment_document_id"""


class CatalogStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def upsert_listing(self, items: Sequence[ListedRegulation]) -> int:
        """Insert or refresh rows from a list page; return how many were new.

        A listing only refreshes what the list shows; status and relations come from
        the detail page. A new PDF link on the list leaves the row for the detail check.
        """
        if not items:
            return 0
        now = _now()
        async with self._db.acquire() as connection:
            known = {
                row["source_id"]
                for row in await connection.fetch(
                    "SELECT source_id FROM regulations WHERE source_id = ANY($1::text[])",
                    [item.source_id for item in items],
                )
            }
            await connection.executemany(
                """
                INSERT INTO regulations (source_id, catalogue, title, law_type, law_number,
                    law_year, issuer, page_url, pdf_url, first_seen_at, last_seen_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, $10)
                ON CONFLICT (source_id) DO UPDATE SET
                    last_seen_at = EXCLUDED.last_seen_at,
                    issuer = COALESCE(regulations.issuer, EXCLUDED.issuer),
                    pdf_url = COALESCE(regulations.pdf_url, EXCLUDED.pdf_url),
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    (
                        item.source_id,
                        item.catalogue,
                        item.title,
                        item.law_type,
                        item.law_number,
                        item.law_year,
                        item.issuer,
                        item.page_url,
                        item.pdf_url,
                        now,
                    )
                    for item in items
                ],
            )
        return len({item.source_id for item in items} - known)

    async def get(self, source_id: str) -> Regulation | None:
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT {_COLUMNS} FROM regulations WHERE source_id = $1", source_id
            )
        return _regulation(row) if row else None

    async def due_for_detail(self, limit: int) -> list[Regulation]:
        """Never-checked rows first (national levels first), then the oldest checks."""
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {_COLUMNS} FROM regulations
                ORDER BY detail_checked_at ASC NULLS FIRST, {_PRIORITY_SQL},
                    law_year DESC NULLS LAST
                LIMIT $1
                """,
                limit,
            )
        return [_regulation(row) for row in rows]

    async def apply_detail(
        self,
        source_id: str,
        *,
        status: str | None,
        issuer: str | None,
        relations: list[dict[str, str]],
        pdf_url: str | None,
        fingerprint: str,
    ) -> list[tuple[str, str | None, str | None]]:
        """Store a detail check; return the changes (field, old, new) found.

        The first check of a row records no changes: there was nothing before it.
        A changed PDF puts the text back in the queue.
        """
        now = _now()
        async with self._db.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                f"SELECT {_COLUMNS}, detail_checked_at FROM regulations "
                "WHERE source_id = $1 FOR UPDATE",
                source_id,
            )
            if row is None:
                return []
            before = _regulation(row)
            first_check = row["detail_checked_at"] is None
            changes: list[tuple[str, str | None, str | None]] = []
            if not first_check and before.fingerprint != fingerprint:
                if before.status != status:
                    changes.append(("status", before.status, status))
                if before.relations != relations:
                    changes.append(
                        (
                            "relations",
                            json.dumps(before.relations, ensure_ascii=False),
                            json.dumps(relations, ensure_ascii=False),
                        )
                    )
                if before.pdf_url != pdf_url:
                    changes.append(("pdf_url", before.pdf_url, pdf_url))
            pdf_changed = (
                before.pdf_url != pdf_url
                and pdf_url is not None
                and before.text_status in {"REGISTERED", "DUPLICATE", "NO_PDF", "FAILED"}
            )
            await connection.execute(
                """
                UPDATE regulations SET status = $2, issuer = COALESCE($3, issuer),
                    relations = $4::jsonb, pdf_url = $5, fingerprint = $6,
                    detail_checked_at = $7, updated_at = $7,
                    changed_at = CASE WHEN $8 THEN $7 ELSE changed_at END,
                    text_status = CASE WHEN $9 THEN 'PENDING' ELSE text_status END,
                    text_attempts = CASE WHEN $9 THEN 0 ELSE text_attempts END
                WHERE source_id = $1
                """,
                source_id,
                status,
                issuer,
                json.dumps(relations, ensure_ascii=False),
                pdf_url,
                fingerprint,
                now,
                bool(changes),
                pdf_changed,
            )
            if changes:
                await connection.executemany(
                    """
                    INSERT INTO regulation_changes
                        (source_id, field, old_value, new_value, changed_at)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    [(source_id, field, old, new, now) for field, old, new in changes],
                )
        return changes

    async def touch_detail(self, source_id: str) -> None:
        """Mark a detail check done without data (page gone), so it is not retried hourly."""
        now = _now()
        async with self._db.acquire() as connection:
            await connection.execute(
                "UPDATE regulations SET detail_checked_at = $2, updated_at = $2 "
                "WHERE source_id = $1",
                source_id,
                now,
            )

    async def count(self) -> int:
        async with self._db.acquire() as connection:
            return int(await connection.fetchval("SELECT count(*) FROM regulations") or 0)

    async def pending_text(self, limit: int) -> list[Regulation]:
        """Checked regulations still without text, national levels and newest first."""
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {_COLUMNS} FROM regulations
                WHERE text_status IN ('PENDING', 'RETRY')
                  AND detail_checked_at IS NOT NULL
                  AND text_attempts < $2
                ORDER BY {_PRIORITY_SQL}, law_year DESC NULLS LAST, source_id
                LIMIT $1
                """,
                limit,
                MAX_TEXT_ATTEMPTS,
            )
        return [_regulation(row) for row in rows]

    async def mark_text(
        self,
        source_id: str,
        text_status: str,
        *,
        pdf_sha256: str | None = None,
        alignment_document_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record the outcome of sending a regulation's text to Alignment.

        RETRY counts an attempt; after MAX_TEXT_ATTEMPTS it becomes FAILED.
        """
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                UPDATE regulations SET
                    text_status = CASE
                        WHEN $2 = 'RETRY' AND text_attempts + 1 >= $6 THEN 'FAILED' ELSE $2 END,
                    text_attempts = text_attempts + 1,
                    pdf_sha256 = COALESCE($3, pdf_sha256),
                    alignment_document_id = COALESCE($4, alignment_document_id),
                    text_error = $5,
                    text_synced_at = $7,
                    updated_at = $7
                WHERE source_id = $1
                """,
                source_id,
                text_status,
                pdf_sha256,
                alignment_document_id,
                (error or "")[:MAX_ERROR] or None,
                MAX_TEXT_ATTEMPTS,
                _now(),
            )

    async def search(
        self,
        *,
        catalogue: str | None,
        year: int | None,
        status: str | None,
        query: str | None,
        page: int,
        limit: int,
    ) -> tuple[list[Regulation], int]:
        conditions: list[str] = []
        params: list[object] = []
        if catalogue:
            params.append(catalogue)
            conditions.append(f"catalogue = ${len(params)}")
        if year:
            params.append(year)
            conditions.append(f"law_year = ${len(params)}")
        if status:
            params.append(status)
            conditions.append(f"status ILIKE ${len(params)}")
        if query:
            params.append(f"%{query}%")
            conditions.append(f"title ILIKE ${len(params)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._db.acquire() as connection:
            total = await connection.fetchval(f"SELECT count(*) FROM regulations {where}", *params)
            rows = await connection.fetch(
                f"""
                SELECT {_COLUMNS} FROM regulations {where}
                ORDER BY {_PRIORITY_SQL}, law_year DESC NULLS LAST, source_id
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                (page - 1) * limit,
            )
        return [_regulation(row) for row in rows], int(total or 0)

    async def stats(self) -> dict[str, object]:
        async with self._db.acquire() as connection:
            per_type = await connection.fetch(
                f"""
                SELECT catalogue,
                    count(*)::int AS total,
                    count(*) FILTER (WHERE detail_checked_at IS NOT NULL)::int AS checked,
                    count(*) FILTER (
                        WHERE text_status IN ('REGISTERED', 'DUPLICATE'))::int AS with_text,
                    count(*) FILTER (WHERE text_status = 'FAILED')::int AS text_failed,
                    count(*) FILTER (WHERE status ILIKE 'dicabut%')::int AS revoked
                FROM regulations GROUP BY catalogue ORDER BY min({_PRIORITY_SQL})
                """
            )
            changes = await connection.fetch(
                """
                SELECT c.source_id, r.title, c.field, c.old_value, c.new_value, c.changed_at
                FROM regulation_changes c JOIN regulations r USING (source_id)
                WHERE c.changed_at > now() - interval '7 days'
                ORDER BY c.changed_at DESC LIMIT 50
                """
            )
        return {
            "catalogues": [dict(row) for row in per_type],
            "recentChanges": [
                {
                    "sourceId": row["source_id"],
                    "title": row["title"],
                    "field": row["field"],
                    "oldValue": row["old_value"],
                    "newValue": row["new_value"],
                    "changedAt": row["changed_at"].isoformat(),
                }
                for row in changes
            ],
        }


__all__ = [
    "CATALOGUE_PRIORITY",
    "MAX_TEXT_ATTEMPTS",
    "CatalogStore",
    "ListedRegulation",
    "Regulation",
]
