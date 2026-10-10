"""Tests for config/app.conf (silent mode and friends)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app_config import (
    clear_app_config_cache,
    get_app_config,
    parse_app_config_text,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_app_config_cache()
    yield
    clear_app_config_cache()


def test_default_silent_mode_is_on() -> None:
    cfg = get_app_config()
    assert cfg.silent_mode is True


def test_parse_silent_mode_true() -> None:
    cfg = parse_app_config_text("SILENT_MODE=true\n")
    assert cfg.silent_mode is True


def test_custom_app_config_path(tmp_path: Path) -> None:
    path = tmp_path / "app.conf"
    path.write_text("SILENT_MODE=yes\n", encoding="utf-8")
    clear_app_config_cache()
    assert get_app_config(path).silent_mode is True
