from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain.models import DocumentState, SourceDocument
from .database import Database

MAX_ERROR_MESSAGE = 500


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StoredDocument:
    source_id: str
    fingerprint: str
    status: DocumentState
    pdf_sha256: str | None
    target_document_id: str | None


class StateStore:
    """Checkpoint per dokumen sumber JDIHN, disimpan di Postgres.

    Schema-nya dimiliki Prisma (`prisma/schema.prisma`); di sini hanya ada SQL biasa.
    Karena itu `verify_schema()` ada: kalau migration belum dijalankan, kegagalannya harus
    berupa pesan yang jelas, bukan `UndefinedTableError` mentah di tengah crawl.

    Catatan penting soal `updated_at`: `@updatedAt` milik Prisma dijalankan di sisi client,
    bukan sebagai trigger database, jadi setiap query di bawah mengisinya sendiri.
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    async def verify_schema(self) -> None:
        async with self._db.acquire() as connection:
            exists = await connection.fetchval("SELECT to_regclass('public.source_documents')")
        if exists is None:
            raise RuntimeError(
                "Tabel collector belum ada. Jalankan migration lebih dulu: yarn db:deploy"
            )

    async def get(self, source_id: str) -> StoredDocument | None:
        async with self._db.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT source_id, fingerprint, status, pdf_sha256, target_document_id
                FROM source_documents WHERE source_id = $1
                """,
                source_id,
            )
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

    async def record(
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
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO source_documents (
                    source_id, source_url, title, fingerprint, attachment_url, institution,
                    legal_hierarchy, matched_keywords, status, pdf_sha256, target_document_id,
                    attempts, error_code, error_message, first_seen_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7::text::"LegalHierarchy", $8, $9::text::"DocumentState", $10, $11,
                    $12, $13, $14, $15, $15
                )
                ON CONFLICT (source_id) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    title = EXCLUDED.title,
                    fingerprint = EXCLUDED.fingerprint,
                    attachment_url = EXCLUDED.attachment_url,
                    institution = EXCLUDED.institution,
                    legal_hierarchy = COALESCE(
                        EXCLUDED.legal_hierarchy, source_documents.legal_hierarchy
                    ),
                    matched_keywords = EXCLUDED.matched_keywords,
                    status = EXCLUDED.status,
                    pdf_sha256 = COALESCE(EXCLUDED.pdf_sha256, source_documents.pdf_sha256),
                    target_document_id = COALESCE(
                        EXCLUDED.target_document_id, source_documents.target_document_id
                    ),
                    attempts = source_documents.attempts + EXCLUDED.attempts,
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    updated_at = EXCLUDED.updated_at
                """,
                document.source_id,
                document.source_url,
                document.title,
                document.fingerprint,
                document.attachment_url,
                document.institution,
                document.legal_hierarchy,
                list(document.matched_keywords),
                status.value,
                pdf_sha256,
                target_document_id,
                int(increment_attempts),
                error_code,
                error_message[:MAX_ERROR_MESSAGE] if error_message else None,
                timestamp,
            )

    async def record_fetch_failure(
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
        async with self._db.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO source_documents (
                    source_id, source_url, title, fingerprint, attachment_url, institution,
                    matched_keywords, status, attempts, error_code, error_message,
                    first_seen_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8::text::"DocumentState", 1, $9, $10, $11, $11
                )
                ON CONFLICT (source_id) DO UPDATE SET
                    attempts = source_documents.attempts + 1,
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    updated_at = EXCLUDED.updated_at
                """,
                document.source_id,
                document.source_url,
                document.title,
                document.fingerprint,
                document.attachment_url,
                document.institution,
                list(document.matched_keywords),
                status.value,
                error_code,
                error_message[:MAX_ERROR_MESSAGE],
                timestamp,
            )

    async def reset_permanent_failures(self) -> int:
        """Kembalikan dokumen FAILED_PERMANENT agar dicoba ulang pada run berikutnya."""
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                """
                UPDATE source_documents
                SET status = $1::text::"DocumentState",
                    error_code = NULL,
                    error_message = NULL,
                    updated_at = $2
                WHERE status = $3::text::"DocumentState"
                RETURNING source_id
                """,
                DocumentState.FAILED_RETRYABLE.value,
                _now(),
                DocumentState.FAILED_PERMANENT.value,
            )
        return len(rows)

    async def status_counts(self) -> Mapping[str, int]:
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                "SELECT status, COUNT(*) AS count FROM source_documents GROUP BY status"
            )
        return {str(row["status"]): int(row["count"]) for row in rows}


__all__ = ["StateStore", "StoredDocument"]
