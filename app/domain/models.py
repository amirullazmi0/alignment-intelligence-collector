from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import CollectorSettings


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


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Parameter satu kali jalan, lepas dari isi `.env`.

    Dipisahkan dari `CollectorSettings` supaya run yang dipicu dashboard bisa memilih
    kementerian dan keyword sendiri tanpa menyentuh berkas `.env`, sementara jalur CLI
    tetap membaca konfigurasi seperti sebelumnya.
    """

    mode: str
    dry_run: bool
    limit: int | None
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    ministry: str | None = None
    ministry_aliases: tuple[str, ...] = ()

    @classmethod
    def for_keywords(
        cls,
        settings: CollectorSettings,
        mode: str,
        *,
        dry_run: bool,
        limit: int | None,
        keywords: tuple[str, ...],
    ) -> RunRequest:
        """Run general: seluruh JDIHN, dibatasi keyword yang diberikan pemanggil.

        Keyword selalu datang dari jadwal atau argumen CLI, tidak pernah dari `.env`,
        sehingga satu deployment bisa punya beberapa run general bertema berbeda tanpa
        saling menyeret tema milik yang lain.
        """
        deduped = _dedupe(keywords)
        if not deduped:
            raise ValueError("Run general wajib punya minimal satu keyword")
        return cls(
            mode=mode,
            dry_run=dry_run,
            limit=limit,
            keywords=deduped,
            exclude_keywords=settings.exclude_keywords,
        )

    @classmethod
    def for_ministry(
        cls,
        settings: CollectorSettings,
        mode: str,
        *,
        dry_run: bool,
        limit: int | None,
        ministry: str,
        aliases: tuple[str, ...] = (),
        extra_keywords: tuple[str, ...] = (),
    ) -> RunRequest:
        """Run bertema satu kementerian.

        Nama kementerian selalu menjadi keyword pertama, ditambah keyword milik
        jadwalnya. `COLLECTOR_EXCLUDE_KEYWORDS` tetap dipakai karena sifatnya pengaman
        yang berlaku menyeluruh, bukan tema.
        """
        return cls(
            mode=mode,
            dry_run=dry_run,
            limit=limit,
            keywords=_dedupe((ministry, *extra_keywords)),
            exclude_keywords=settings.exclude_keywords,
            ministry=ministry,
            ministry_aliases=_dedupe(aliases),
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
    institution: str | None = None
    legal_hierarchy: str | None = None


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
    ministry: str | None = None
    ministry_matched: int = 0
    preview: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """Ringkasan run dalam bentuk yang dikirim ke CLI maupun ke Nest."""
        return {
            "mode": self.mode,
            "dry_run": self.dry_run,
            "discovered": self.discovered,
            "matched": self.matched,
            "uploaded": self.uploaded,
            "duplicates": self.duplicates,
            "skipped_existing": self.skipped_existing,
            "skipped_no_pdf": self.skipped_no_pdf,
            "source_changed": self.source_changed,
            "failed": self.failed,
            "ministry": self.ministry,
            "ministry_matched": self.ministry_matched,
        }

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
