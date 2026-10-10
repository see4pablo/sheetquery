"""Shared paths for integration fixtures (synthetic workbooks only)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def fake_sanjose_path() -> Path:
    """PSCV San José-style export: title rows, then EDAD / SEXO header."""
    return FIXTURES_DIR / "fake_sanjose.csv"


@pytest.fixture(scope="session")
def fake_diabetes_yeco_path() -> Path:
    """diabetes-yeco foot-risk report: longer preamble, Edad a la Aplicación."""
    return FIXTURES_DIR / "fake_diabetes_yeco.csv"
