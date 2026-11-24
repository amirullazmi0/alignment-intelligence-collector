from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import CollectorSettings
from app.domain.models import RunRequest


def test_password_tidak_bocor_lewat_repr() -> None:
    settings = CollectorSettings(ALIGNMENT_PASSWORD=SecretStr("private-value"))

    assert "private-value" not in repr(settings)


def test_tema_tingkat_environment_sudah_tidak_ada() -> None:
    """Keyword selalu datang dari jadwal atau argumen CLI, tidak pernah dari .env.

    Sebelumnya COLLECTOR_TOPIC ikut terbawa ke run mana pun yang tidak menyebut
    kementerian, sehingga run yang dikira bertema lain diam-diam memakai tema lama.
    """
    settings = CollectorSettings(ALIGNMENT_PASSWORD=SecretStr("private-value"))

    assert not hasattr(settings, "keywords")
    assert not hasattr(RunRequest, "from_settings")


def test_run_general_memakai_keyword_yang_diberikan() -> None:
    settings = CollectorSettings(
        COLLECTOR_EXCLUDE_KEYWORDS="rektor",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )

    request = RunRequest.for_keywords(
        settings, "backfill", dry_run=True, limit=None, keywords=("hotel", "hotel", "restoran")
    )

    assert request.keywords == ("hotel", "restoran")
    assert request.exclude_keywords == ("rektor",)
    assert request.ministry is None


def test_run_request_for_ministry_hanya_memakai_keyword_jadwalnya() -> None:
    settings = CollectorSettings(
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
    assert request.exclude_keywords == ("rektor",), "exclude tetap dipakai sebagai pengaman"
    assert request.ministry == "Kementerian Pariwisata"
    assert request.ministry_aliases == ("Kementerian Pariwisata dan Ekonomi Kreatif",)


def test_token_pasal_wajib_untuk_sumber_pasal() -> None:
    settings = CollectorSettings(_env_file=None, DATABASE_URL="postgresql://localhost/x")
    with pytest.raises(ValueError, match="PASAL_API_TOKEN"):
        settings.require_pasal_token()


def test_url_pasal_harus_domain_pasal_id() -> None:
    with pytest.raises(ValueError, match="PASAL_API_BASE_URL"):
        CollectorSettings(_env_file=None, PASAL_API_BASE_URL="https://contoh.example/api")
