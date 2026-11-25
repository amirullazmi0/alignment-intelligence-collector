from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import httpx
import pytest

from app.clients.alignment import AlignmentClient
from app.clients.pdf import PdfDownloader
from app.clients.peraturan_go_id import PeraturanGoIdClient
from app.config import CollectorSettings
from app.services.catalog_sync import CHECKPOINT_SOURCE, CatalogSync, regulation_document
from app.storage.catalog import CatalogStore, ListedRegulation, Regulation
from app.storage.state import StateStore

FIXTURES = Path(__file__).parent.parent / "fixtures" / "peraturan_go_id"
BASE = "https://peraturan.go.id"
EMPTY = "<html><body><p>Tidak ada data</p></body></html>"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _regulation(**overrides: object) -> Regulation:
    values: dict[str, object] = {
        "source_id": "pgo:uu-no-13-tahun-2003",
        "catalogue": "uu",
        "title": "Undang-Undang Nomor 13 Tahun 2003 tentang Ketenagakerjaan",
        "law_type": "Undang-Undang",
        "law_number": "13",
        "law_year": 2003,
        "issuer": None,
        "status": "Berlaku",
        "relations": [],
        "page_url": f"{BASE}/id/uu-no-13-tahun-2003",
        "pdf_url": f"{BASE}/files/UU+13-2003.pdf",
        "fingerprint": "old",
        "text_status": "REGISTERED",
        "pdf_sha256": None,
        "alignment_document_id": "doc-1",
    }
    values.update(overrides)
    return Regulation(**values)  # type: ignore[arg-type]


class MemoryCatalog(CatalogStore):
    """In-memory CatalogStore: these tests cover the sync logic, not its SQL."""

    def __init__(self, rows: Sequence[Regulation] = ()) -> None:  # no database
        self.rows = {row.source_id: row for row in rows}
        self.listed: list[ListedRegulation] = []
        self.details: list[dict[str, object]] = []
        self.marks: list[tuple[str, str, str | None]] = []
        self.next_changes: list[tuple[str, str | None, str | None]] = []

    async def upsert_listing(self, items: Sequence[ListedRegulation]) -> int:
        new = [item for item in items if item.source_id not in {i.source_id for i in self.listed}]
        self.listed.extend(new)
        return len(new)

    async def due_for_detail(self, limit: int) -> list[Regulation]:
        return list(self.rows.values())[:limit]

    async def apply_detail(
        self, source_id: str, **values: object
    ) -> list[tuple[str, str | None, str | None]]:
        self.details.append({"source_id": source_id, **values})
        return self.next_changes

    async def touch_detail(self, source_id: str) -> None:
        self.details.append({"source_id": source_id, "missing": True})

    async def pending_text(self, limit: int) -> list[Regulation]:
        return list(self.rows.values())[:limit]

    async def mark_text(
        self,
        source_id: str,
        text_status: str,
        *,
        pdf_sha256: str | None = None,
        alignment_document_id: str | None = None,
        error: str | None = None,
    ) -> None:
        self.marks.append((source_id, text_status, alignment_document_id))


class MemoryCheckpoints(StateStore):
    def __init__(self) -> None:  # no database
        self.pages: dict[tuple[str, str], int] = {}

    async def get_checkpoint(self, source: str, catalogue: str) -> int:
        return self.pages.get((source, catalogue), 1)

    async def set_checkpoint(self, source: str, catalogue: str, next_page: int) -> None:
        self.pages[(source, catalogue)] = next_page


def _settings(**values: object) -> CollectorSettings:
    return CollectorSettings(
        _env_file=None,
        DATABASE_URL="postgresql://localhost/tidak-dipakai",
        PERATURAN_GO_ID_REQUEST_DELAY_SECONDS=1,
        **values,  # type: ignore[arg-type]
    )


def _sync(
    catalog: MemoryCatalog,
    handler: httpx.MockTransport,
    *,
    state: StateStore | None = None,
    tmp_path: Path | None = None,
) -> CatalogSync:
    client = httpx.AsyncClient(transport=handler, follow_redirects=True)

    def pgo() -> AbstractAsyncContextManager[PeraturanGoIdClient]:
        return PeraturanGoIdClient(BASE, 5, 0, client=client)

    def alignment() -> AbstractAsyncContextManager[AlignmentClient]:
        return AlignmentClient("https://alignment.example/api", "bot@x.test", "x", 5, client)

    def downloader() -> AbstractAsyncContextManager[PdfDownloader]:
        return PdfDownloader(5, 1024, client, tmp_path or Path("."))

    return CatalogSync(
        _settings(),
        catalog,
        state or MemoryCheckpoints(),
        pgo_factory=pgo,
        alignment_factory=alignment,
        downloader_factory=downloader,
    )


async def test_sync_new_reads_first_pages_of_every_type() -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(f"{request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/uu" and request.url.params["page"] == "1":
            return httpx.Response(200, text=_fixture("uu_page2.html"))
        return httpx.Response(200, text=EMPTY)

    catalog = MemoryCatalog()
    report = await _sync(catalog, httpx.MockTransport(handler)).run("sync_new")

    assert report.error is None
    assert report.counts["new"] == 20
    # Page 1 of uu was all new, so page 2 is read too; other types stop at page 1.
    assert "/uu?page=2" in requested
    assert "/perda?page=1" in requested and "/perda?page=2" not in requested
    first = next(r for r in catalog.listed if r.source_id == "pgo:uu-no-4-tahun-2025")
    assert first.law_number == "4"
    assert first.law_year == 2025
    assert first.page_url == f"{BASE}/id/uu-no-4-tahun-2025"
    assert first.pdf_url == f"{BASE}/files/uu-no-4-tahun-2025.pdf"


async def test_sweep_walks_every_page_and_restarts_after_the_end() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/uu" and request.url.params["page"] in {"1", "2"}:
            return httpx.Response(200, text=_fixture("uu_page2.html"))
        return httpx.Response(200, text=EMPTY)

    state = MemoryCheckpoints()
    report = await _sync(MemoryCatalog(), httpx.MockTransport(handler), state=state).run("sweep")

    assert report.error is None
    assert report.counts["completed"] == 6
    assert state.pages[(CHECKPOINT_SOURCE, "uu")] == 1


async def test_changed_status_is_stored_and_pushed_to_alignment() -> None:
    patches: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/id/uu-no-13-tahun-2003":
            return httpx.Response(200, text=_fixture("uu_13_2003.html"))
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        if request.url.path.endswith("/v1/documents/external"):
            assert request.method == "PATCH"
            patches.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"documentId": "doc-1"}})
        return httpx.Response(404)

    catalog = MemoryCatalog([_regulation(status="Dicabut")])
    catalog.next_changes = [("status", "Dicabut", "Berlaku")]
    report = await _sync(catalog, httpx.MockTransport(handler)).run("refresh_details")

    assert report.error is None
    assert report.counts == {"checked": 1, "changed": 1, "pushed": 1}
    detail = catalog.details[0]
    assert detail["status"] == "Berlaku"
    assert detail["pdf_url"] == f"{BASE}/files/UU+13-2003.pdf"
    assert {"kind": "Mencabut", "target": "Undang-Undang Nomor 25 Tahun 1997"} in detail[
        "relations"
    ]  # type: ignore[operator]
    assert patches[0]["sourceUrl"] == f"{BASE}/id/uu-no-13-tahun-2003"
    assert patches[0]["effectiveStatus"] == "Berlaku"


async def test_unchanged_detail_sends_nothing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/id/uu-no-13-tahun-2003":
            return httpx.Response(200, text=_fixture("uu_13_2003.html"))
        raise AssertionError(f"unexpected request {request.url}")

    catalog = MemoryCatalog([_regulation()])
    report = await _sync(catalog, httpx.MockTransport(handler)).run("refresh_details")
    assert report.counts == {"checked": 1}


async def test_missing_detail_page_is_marked_checked() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        return httpx.Response(404)

    catalog = MemoryCatalog([_regulation()])
    report = await _sync(catalog, httpx.MockTransport(handler)).run("refresh_details")
    assert report.counts == {"missing": 1}
    assert catalog.details == [{"source_id": "pgo:uu-no-13-tahun-2003", "missing": True}]


async def test_text_is_registered_by_link_and_no_pdf_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr("app.clients.pdf._validate_public_https_url", allow)
    registered: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/files/"):
            return httpx.Response(
                200, content=b"%PDF-1.7 x", headers={"content-type": "application/pdf"}
            )
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        if request.url.path.endswith("/v1/documents/external/queue"):
            return httpx.Response(200, json={"data": {"pending": 0, "processing": 0}})
        registered.append(json.loads(request.content))
        return httpx.Response(201, json={"data": {"documentId": "doc-9"}})

    catalog = MemoryCatalog(
        [
            _regulation(text_status="PENDING", alignment_document_id=None, relations=[]),
            _regulation(source_id="pgo:pp-no-1-tahun-2026", pdf_url=None, text_status="PENDING"),
        ]
    )
    report = await _sync(catalog, httpx.MockTransport(handler), tmp_path=tmp_path).run(
        "ingest_text"
    )

    assert report.error is None
    assert report.counts == {"queueBefore": 0, "registered": 1, "noPdf": 1}
    assert catalog.marks == [
        ("pgo:uu-no-13-tahun-2003", "REGISTERED", "doc-9"),
        ("pgo:pp-no-1-tahun-2026", "NO_PDF", None),
    ]
    assert registered[0]["pdfUrl"] == f"{BASE}/files/UU+13-2003.pdf"
    assert registered[0]["sourceName"] == "peraturan.go.id"
    assert registered[0]["effectiveStatus"] == "Berlaku"
    assert list(tmp_path.iterdir()) == []


async def test_a_failing_job_reports_its_error_and_releases_the_lock() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=EMPTY)

    class Broken(MemoryCatalog):
        async def due_for_detail(self, limit: int) -> list[Regulation]:
            raise RuntimeError("db down")

    sync = _sync(Broken(), httpx.MockTransport(handler))
    report = await sync.run("refresh_details")
    assert report.error == "RuntimeError: db down"
    assert not sync.busy


def test_regulation_document_carries_the_official_metadata() -> None:
    document = regulation_document(_regulation())
    assert document.source_url == f"{BASE}/id/uu-no-13-tahun-2003"
    assert document.document_number == "Nomor 13 Tahun 2003"
    assert document.source_name == "peraturan.go.id"


@pytest.mark.parametrize(("waiting", "expected_sent"), [(10, 0), (8, 2), (0, 3)])
async def test_ingest_text_only_fills_alignment_queue_up_to_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, waiting: int, expected_sent: int
) -> None:
    """A full queue gets nothing; an emptier one is topped up to PGO_QUEUE_TARGET (10)."""

    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr("app.clients.pdf._validate_public_https_url", allow)
    sent: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/files/"):
            return httpx.Response(
                200, content=b"%PDF-1.7 x", headers={"content-type": "application/pdf"}
            )
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"data": {"accessToken": "token"}})
        if request.url.path.endswith("/v1/documents/external/queue"):
            return httpx.Response(200, json={"data": {"pending": waiting, "processing": 0}})
        sent.append(json.loads(request.content)["pdfUrl"])
        return httpx.Response(201, json={"data": {"documentId": f"doc-{len(sent)}"}})

    rows = [
        _regulation(
            source_id=f"pgo:uu-no-{i}-tahun-2025",
            pdf_url=f"{BASE}/files/uu-{i}.pdf",
            text_status="PENDING",
        )
        for i in range(3)
    ]

    class Limited(MemoryCatalog):
        async def pending_text(self, limit: int) -> list[Regulation]:
            return list(self.rows.values())[:limit]

    report = await _sync(Limited(rows), httpx.MockTransport(handler), tmp_path=tmp_path).run(
        "ingest_text"
    )

    assert report.error is None
    assert len(sent) == expected_sent
    assert report.counts["queueBefore"] == waiting
