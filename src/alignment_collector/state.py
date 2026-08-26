from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import DocumentState, RunSummary, SourceDocument


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class StoredDocument:
    source_id: str
    fingerprint: str
    status: DocumentState
    pdf_sha256: str | None
    target_document_id: str | None


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary_json TEXT
                );
                CREATE TABLE IF NOT EXISTS source_documents (
                    source_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    attachment_url TEXT,
                    matched_keywords TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pdf_sha256 TEXT,
                    target_document_id TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_documents_status
                ON source_documents(status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def start_run(self, mode: str, dry_run: bool) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs(mode, dry_run, started_at) VALUES (?, ?, ?)",
                (mode, int(dry_run), _now()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Gagal membuat run collector")
            return cursor.lastrowid

    def finish_run(self, run_id: int, summary: RunSummary) -> None:
        payload = {
            "discovered": summary.discovered,
            "duplicates": summary.duplicates,
            "failed": summary.failed,
            "matched": summary.matched,
            "skipped_existing": summary.skipped_existing,
            "skipped_no_pdf": summary.skipped_no_pdf,
            "source_changed": summary.source_changed,
            "uploaded": summary.uploaded,
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ?, summary_json = ? WHERE id = ?",
                (_now(), json.dumps(payload, sort_keys=True), run_id),
            )

    def get(self, source_id: str) -> StoredDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_id, fingerprint, status, pdf_sha256, target_document_id
                FROM source_documents WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredDocument(
            source_id=str(row["source_id"]),
            fingerprint=str(row["fingerprint"]),
            status=DocumentState(str(row["status"])),
            pdf_sha256=str(row["pdf_sha256"]) if row["pdf_sha256"] else None,
            target_document_id=(
                str(row["target_document_id"]) if row["target_document_id"] else None
            ),
        )

    def record(
        self,
        document: SourceDocument,
        status: DocumentState,
        *,
        pdf_sha256: str | None = None,
        target_document_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_documents(
                    source_id, source_url, title, fingerprint, attachment_url,
                    matched_keywords, status, pdf_sha256, target_document_id,
                    attempts, error_code, error_message, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_url = excluded.source_url,
                    title = excluded.title,
                    fingerprint = excluded.fingerprint,
                    attachment_url = excluded.attachment_url,
                    matched_keywords = excluded.matched_keywords,
                    status = excluded.status,
                    pdf_sha256 = COALESCE(excluded.pdf_sha256, source_documents.pdf_sha256),
                    target_document_id = COALESCE(
                        excluded.target_document_id, source_documents.target_document_id
                    ),
                    attempts = source_documents.attempts + excluded.attempts,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    document.source_id,
                    document.source_url,
                    document.title,
                    document.fingerprint,
                    document.attachment_url,
                    json.dumps(document.matched_keywords, ensure_ascii=False),
                    status.value,
                    pdf_sha256,
                    target_document_id,
                    int(increment_attempts),
                    error_code,
                    error_message[:500] if error_message else None,
                    timestamp,
                    timestamp,
                ),
            )

    def record_fetch_failure(
        self,
        document: SourceDocument,
        status: DocumentState,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Catat kegagalan yang terjadi sebelum halaman detail berhasil di-parse.

        Baris yang sudah ada hanya diperbarui pada kolom error dan attempts. Status serta
        fingerprint hasil parse sengaja dipertahankan: stub document memakai rumus
        fingerprint yang berbeda, sehingga menimpanya akan memicu SOURCE_CHANGED palsu dan
        menurunkan status dokumen yang sebelumnya sudah berhasil diunggah.
        """
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_documents(
                    source_id, source_url, title, fingerprint, attachment_url,
                    matched_keywords, status, pdf_sha256, target_document_id,
                    attempts, error_code, error_message, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 1, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    attempts = source_documents.attempts + 1,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    document.source_id,
                    document.source_url,
                    document.title,
                    document.fingerprint,
                    document.attachment_url,
                    json.dumps(document.matched_keywords, ensure_ascii=False),
                    status.value,
                    error_code,
                    error_message[:500],
                    timestamp,
                    timestamp,
                ),
            )

    def reset_permanent_failures(self) -> int:
        """Kembalikan dokumen FAILED_PERMANENT agar dicoba ulang pada run berikutnya."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE source_documents
                SET status = ?, error_code = NULL, error_message = NULL, updated_at = ?
                WHERE status = ?
                """,
                (
                    DocumentState.FAILED_RETRYABLE.value,
                    _now(),
                    DocumentState.FAILED_PERMANENT.value,
                ),
            )
            return cursor.rowcount

    def status_counts(self) -> Mapping[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM source_documents GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def last_run(self) -> Mapping[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, mode, dry_run, started_at, finished_at, summary_json
                FROM runs ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        summary: object = json.loads(str(row["summary_json"])) if row["summary_json"] else None
        return {
            "id": int(row["id"]),
            "mode": str(row["mode"]),
            "dry_run": bool(row["dry_run"]),
            "started_at": str(row["started_at"]),
            "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
            "summary": summary,
        }
