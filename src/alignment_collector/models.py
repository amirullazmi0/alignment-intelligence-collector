from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class DocumentState(StrEnum):
    DISCOVERED = "DISCOVERED"
    SKIPPED_NO_PDF = "SKIPPED_NO_PDF"
    UPLOADED = "UPLOADED"
    DUPLICATE = "DUPLICATE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    PARSE_FAILED = "PARSE_FAILED"
    SOURCE_CHANGED = "SOURCE_CHANGED"


@dataclass(frozen=True, slots=True)
class SearchItem:
    source_id: str
    title: str
    document_number: str | None
    category: str
    subtitle: str | None
    description: str | None
    institution: str | None
    effective_status: str | None
    law_type: str | None
    law_year: int | None

    @property
    def searchable_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.title,
                self.document_number,
                self.subtitle,
                self.description,
                self.institution,
                self.law_type,
            )
            if value
        )


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_id: str
    source_url: str
    title: str
    attachment_url: str | None
    category: str
    document_number: str | None
    law_type: str | None
    law_number: str | None
    law_year: int | None
    effective_status: str | None
    matched_keywords: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class DownloadedPdf:
    path: Path
    filename: str
    sha256: str
    size_bytes: int
    content_type: str


@dataclass(frozen=True, slots=True)
class UploadResult:
    document_id: str | None
    duplicate: bool


@dataclass(slots=True)
class RunSummary:
    mode: str
    dry_run: bool
    discovered: int = 0
    matched: int = 0
    uploaded: int = 0
    duplicates: int = 0
    skipped_existing: int = 0
    skipped_no_pdf: int = 0
    source_changed: int = 0
    failed: int = 0
    preview: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return (
            self.uploaded
            + self.duplicates
            + self.skipped_existing
            + self.skipped_no_pdf
            + self.source_changed
            + self.failed
        )
