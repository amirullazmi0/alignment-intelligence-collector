from __future__ import annotations

import logging

from app.logging_config import LevelColorFormatter, should_use_color


def test_color_formatter_matches_existing_python_service() -> None:
    record = logging.LogRecord("collector", logging.WARNING, "", 0, "hati-hati", (), None)
    formatted = LevelColorFormatter(use_color=True).format(record)
    assert "\033[33;1m[WARNING]\033[0m" in formatted


def test_color_mode_validation() -> None:
    assert should_use_color("always", object()) is True
    assert should_use_color("never", object()) is False
