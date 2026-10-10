"""Unit tests for config/column_aliases.conf loading and resolution."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from column_aliases import (
    clear_alias_cache,
    get_aliases,
    parse_aliases_text,
    pick_column_from_candidates,
    resolve_column,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_alias_cache()
    yield
    clear_alias_cache()


def test_parse_single_and_multi_header_lines() -> None:
    text = """
    # comment
    DIABETES="DIAGNOSTICO PSCV DE DIABETES MELLITUS 2 (SI, NO)"
    HTA="DIAGNOSTICO  PSCV DE HTA (SI,NO)", "DIAGNOSTICO  HTA (SI, NO)"
    RAC=VALOR RAC
    RCV="RIESGO CARDIOVASCULAR  (BAJO, MODERADO, ALTO)"
    """
    aliases = parse_aliases_text(text)
    assert aliases["DIABETES"] == ["DIAGNOSTICO PSCV DE DIABETES MELLITUS 2 (SI, NO)"]
    assert aliases["HTA"] == [
        "DIAGNOSTICO  PSCV DE HTA (SI,NO)",
        "DIAGNOSTICO  HTA (SI, NO)",
    ]
    assert aliases["RAC"] == ["VALOR RAC"]
    assert aliases["RCV"] == ["RIESGO CARDIOVASCULAR  (BAJO, MODERADO, ALTO)"]


def test_default_config_has_built_in_aliases() -> None:
    aliases = get_aliases()
    assert "DIABETES" in aliases
    assert "HYPERTENSION" in aliases
    assert len(aliases["HYPERTENSION"]) == 2
    assert "INSULIN" in aliases
    assert len(aliases["INSULIN"]) == 2


def test_pick_prefers_header_present_in_workbook() -> None:
    frame = pd.DataFrame(
        {
            "DIAGNOSTICO  HTA (SI, NO)": ["SI"],
            "EDAD": ["60"],
        }
    )
    picked = pick_column_from_candidates(
        frame,
        [
            "DIAGNOSTICO  PSCV DE HTA (SI,NO)",
            "DIAGNOSTICO  HTA (SI, NO)",
        ],
    )
    assert picked == "DIAGNOSTICO  HTA (SI, NO)"


def test_resolve_hypertension_from_config_on_sanjose(
    fake_sanjose_path: Path,
) -> None:
    from pscv_classification import load_data

    frame = load_data(fake_sanjose_path, None, None).dropna(how="all")
    assert resolve_column(frame, "HYPERTENSION") == "DIAGNOSTICO  PSCV DE HTA (SI,NO)"
    assert resolve_column(frame, "RCV").startswith("RIESGO CARDIOVASCULAR")


def test_resolve_custom_aliases_path(tmp_path: Path) -> None:
    conf = tmp_path / "aliases.conf"
    conf.write_text(
        'FOO="Columna Foo (SI, NO)"\nBAR="Columna Bar", "Columna Bar Alt"\n',
        encoding="utf-8",
    )
    frame = pd.DataFrame({"Columna Bar Alt": ["x"], "other": ["y"]})
    clear_alias_cache()
    assert resolve_column(frame, "BAR", aliases_path=conf) == "Columna Bar Alt"
    with pytest.raises(ValueError, match="FOO"):
        resolve_column(frame, "FOO", aliases_path=conf)
