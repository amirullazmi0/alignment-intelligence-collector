from __future__ import annotations

from pathlib import Path

from alignment_collector.models import DocumentState, RunSummary, SourceDocument
from alignment_collector.state import StateStore


def _document() -> SourceDocument:
    return SourceDocument(
        source_id="42",
        source_url="https://jdihn.go.id/doc/42",
        title="Peraturan Pendidikan",
        attachment_url="https://files.example.org/42.pdf",
        category="peraturan",
        document_number="Nomor 7 Tahun 2024",
        law_type="PERATURAN MENTERI",
        law_number="7",
        law_year=2024,
        effective_status="Berlaku",
        matched_keywords=("pendidikan",),
        fingerprint="fingerprint",
    )


def test_state_is_resumable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.initialize()
    run_id = store.start_run("backfill", False)
    store.record(
        _document(),
        DocumentState.UPLOADED,
        pdf_sha256="sha",
        target_document_id="target-id",
        increment_attempts=True,
    )
    summary = RunSummary(mode="backfill", dry_run=False, uploaded=1)
    store.finish_run(run_id, summary)

    stored = store.get("42")
    assert stored is not None
    assert stored.status == DocumentState.UPLOADED
    assert stored.target_document_id == "target-id"
    assert store.status_counts() == {"UPLOADED": 1}
    assert store.last_run() is not None


def test_fetch_failure_preserves_parsed_status_and_fingerprint(tmp_path: Path) -> None:
    """Blip jaringan tidak boleh menurunkan dokumen yang sudah berhasil diunggah."""
    store = StateStore(tmp_path / "state.sqlite3")
    store.initialize()
    store.record(
        _document(),
        DocumentState.UPLOADED,
        pdf_sha256="sha",
        target_document_id="target-id",
        increment_attempts=True,
    )

    stub = SourceDocument(
        source_id="42",
        source_url="https://jdihn.go.id/doc/42",
        title="Peraturan Pendidikan",
        attachment_url=None,
        category="peraturan",
        document_number=None,
        law_type=None,
        law_number=None,
        law_year=None,
        effective_status=None,
        matched_keywords=(),
        fingerprint="fingerprint-stub-berbeda",
    )
    store.record_fetch_failure(
        stub,
        DocumentState.FAILED_RETRYABLE,
        error_code="JDIHN_NETWORK_ERROR",
        error_message="connection reset",
    )

    stored = store.get("42")
    assert stored is not None
    assert stored.status == DocumentState.UPLOADED
    assert stored.fingerprint == "fingerprint"
    assert stored.target_document_id == "target-id"


def test_fetch_failure_inserts_new_document_as_retryable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.initialize()
    store.record_fetch_failure(
        _document(),
        DocumentState.FAILED_RETRYABLE,
        error_code="JDIHN_NETWORK_ERROR",
        error_message="connection reset",
    )

    stored = store.get("42")
    assert stored is not None
    assert stored.status == DocumentState.FAILED_RETRYABLE


def test_reset_permanent_failures_makes_documents_retryable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.initialize()
    store.record(
        _document(),
        DocumentState.FAILED_PERMANENT,
        error_code="UnsafePdfUrlError",
        error_message="Host PDF tidak dapat di-resolve",
        increment_attempts=True,
    )

    assert store.reset_permanent_failures() == 1

    stored = store.get("42")
    assert stored is not None
    assert stored.status == DocumentState.FAILED_RETRYABLE
    assert store.reset_permanent_failures() == 0
