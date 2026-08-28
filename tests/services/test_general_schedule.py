from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.api.routes.schedules import GENERAL_LABEL, _resolve_target
from app.config import CollectorSettings
from app.domain.models import RunRequest
from app.services.runner import _request_for
from app.storage.repository import Run


def _settings() -> CollectorSettings:
    return CollectorSettings(
        COLLECTOR_EXCLUDE_KEYWORDS="rektor",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )


def _run(ministry: str | None, keywords: tuple[str, ...]) -> Run:
    return Run(
        id="run-1",
        schedule_id="schedule-1",
        ministry=ministry,
        ministry_label=ministry or GENERAL_LABEL,
        keywords=keywords,
        trigger="CRON",
        status="QUEUED",
        dry_run=False,
        document_limit=10,
        started_at=None,
        finished_at=None,
        summary=None,
        error_message=None,
        created_at=None,
    )


def test_kementerian_dikenali_menjadi_sasaran() -> None:
    target = _resolve_target("Kementerian Pariwisata", ())

    assert target.ministry == "Kementerian Pariwisata"
    assert target.label


def test_ministry_kosong_berarti_general() -> None:
    target = _resolve_target(None, ("hotel",))

    assert target.ministry is None
    assert target.label == GENERAL_LABEL
    assert target.aliases == ()


def test_general_tanpa_keyword_ditolak() -> None:
    """Tanpa keyword, jadwal general akan menyusuri seluruh JDIHN tanpa batas."""
    with pytest.raises(HTTPException) as error:
        _resolve_target("", ())

    assert error.value.status_code == 422


def test_general_dengan_keyword_kosong_juga_ditolak() -> None:
    with pytest.raises(HTTPException) as error:
        _resolve_target("   ", ("   ",))

    assert error.value.status_code == 422


def test_kementerian_tak_dikenal_ditolak() -> None:
    with pytest.raises(HTTPException) as error:
        _resolve_target("Kementerian Yang Tidak Ada", ())

    assert error.value.status_code == 422


def test_run_general_memakai_keyword_jadwalnya_sendiri() -> None:
    """Bukan tema dari .env: satu deployment boleh punya beberapa jadwal general."""
    request = _request_for(_settings(), _run(None, ("hotel", "restoran")))

    assert request.ministry is None
    assert request.keywords == ("hotel", "restoran")
    assert request.exclude_keywords == ("rektor",)


def test_run_kementerian_tetap_menyertakan_namanya_sebagai_keyword() -> None:
    request = _request_for(_settings(), _run("Kementerian Pariwisata", ("hotel",)))

    assert request.ministry == "Kementerian Pariwisata"
    assert "Kementerian Pariwisata" in request.keywords
    assert "hotel" in request.keywords


def test_run_general_tanpa_keyword_gagal_keras() -> None:
    with pytest.raises(ValueError):
        RunRequest.for_keywords(_settings(), "backfill", dry_run=False, limit=1, keywords=())
