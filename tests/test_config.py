from __future__ import annotations

from pydantic import SecretStr

from alignment_collector.config import CollectorSettings


def test_topic_is_automatically_a_keyword() -> None:
    settings = CollectorSettings(
        COLLECTOR_TOPIC="pendidikan",
        COLLECTOR_KEYWORDS="sekolah,pendidikan,guru",
        ALIGNMENT_PASSWORD=SecretStr("private-value"),
    )
    assert settings.keywords == ("pendidikan", "sekolah", "guru")
    assert "private-value" not in repr(settings)
