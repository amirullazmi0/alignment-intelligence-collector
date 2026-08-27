from __future__ import annotations

from pydantic import SecretStr

from app.config import CollectorSettings
from app.domain.models import RunRequest


def test_topic_is_automatically_a_keyword() -> None:
    settings = CollectorSettings(
        COLLECTOR_TOPIC="pendidikan",
        COLLECTOR_KEYWORDS="sekolah,pendidikan,guru",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )
    assert settings.keywords == ("pendidikan", "sekolah", "guru")
    assert "private-value" not in repr(settings)


def test_run_request_from_settings_reproduces_env_keywords() -> None:
    settings = CollectorSettings(
        COLLECTOR_TOPIC="pendidikan",
        COLLECTOR_KEYWORDS="sekolah,guru",
        COLLECTOR_EXCLUDE_KEYWORDS="rektor",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )

    request = RunRequest.from_settings(settings, "backfill", dry_run=True, limit=None)

    assert request.keywords == ("pendidikan", "sekolah", "guru")
    assert request.exclude_keywords == ("rektor",)
    assert request.ministry is None


def test_run_request_for_ministry_ignores_env_topic() -> None:
    """Run kementerian tidak boleh menyeret tema lama yang kebetulan ada di .env."""
    settings = CollectorSettings(
        COLLECTOR_TOPIC="pendidikan",
        COLLECTOR_KEYWORDS="sekolah,guru",
        COLLECTOR_EXCLUDE_KEYWORDS="rektor",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )

    request = RunRequest.for_ministry(
        settings,
        "backfill",
        dry_run=True,
        limit=5,
        ministry="Kementerian Pariwisata",
        aliases=("Kementerian Pariwisata dan Ekonomi Kreatif",),
        extra_keywords=("hotel", "hotel"),
    )

    assert request.keywords == ("Kementerian Pariwisata", "hotel")
    assert "pendidikan" not in request.keywords
    assert request.exclude_keywords == ("rektor",), "exclude tetap dipakai sebagai pengaman"
    assert request.ministry == "Kementerian Pariwisata"
    assert request.ministry_aliases == ("Kementerian Pariwisata dan Ekonomi Kreatif",)
