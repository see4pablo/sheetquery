"""Integration tests: offset CSV headers and a few basic filter queries.

Fixtures under tests/fixtures/ are synthetic (fake names and document numbers).
They mirror the two real export layouts in data/:
  - sanjose.csv (PSCV; header a few rows down)
  - diabetes-yeco.csv (foot-risk report; header further down, different age/sex names)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from age_sex_segmentation import age_sex_count_table, totals_summary
from interactive_query import resolve_column
from pscv_classification import (
    _infer_csv_skiprows,
    load_data,
    resolve_age_sex_columns,
)
from query_conditions import build_expression_mask


def _load(path: Path):
    frame = load_data(path, separator=None, sheet=None).dropna(how="all")
    age_column, sex_column = resolve_age_sex_columns(frame)
    return frame, age_column, sex_column


def _match_count(frame, expression: str) -> int:
    mask, _descriptions, _label = build_expression_mask(
        frame, expression, resolve_column
    )
    return int(mask.sum())


# --- Header detection / load -------------------------------------------------


def test_infer_skiprows_pscv_sanjose_style(fake_sanjose_path: Path) -> None:
    # Three preamble rows; real header is on the fourth line (skiprows=3).
    assert _infer_csv_skiprows(fake_sanjose_path, ",") == 3


def test_infer_skiprows_diabetes_yeco_style(fake_diabetes_yeco_path: Path) -> None:
    # Title block through row 7; header is on the eighth line (skiprows=7).
    assert _infer_csv_skiprows(fake_diabetes_yeco_path, ",") == 7


def test_load_sanjose_finds_age_sex_and_rows(fake_sanjose_path: Path) -> None:
    frame, age_column, sex_column = _load(fake_sanjose_path)

    assert age_column == "EDAD"
    assert sex_column == "SEXO"
    assert len(frame) == 6
    assert "RIESGO CARDIOVASCULAR  (BAJO, MODERADO, ALTO)" in frame.columns
    assert set(frame["NOMBRE"]) == {
        "ANA FALSA UNO",
        "BRUNO FALSO DOS",
        "CARLA FALSA TRES",
        "DIEGO FALSO CUATRO",
        "ELENA FALSA CINCO",
        "FABIO FALSO SEIS",
    }


def test_load_diabetes_yeco_finds_age_sex_and_rows(
    fake_diabetes_yeco_path: Path,
) -> None:
    frame, age_column, sex_column = _load(fake_diabetes_yeco_path)

    assert age_column == "Edad a la Aplicación"
    assert sex_column == "Sexo"
    assert len(frame) == 6
    assert "Riesgo Resultante" in frame.columns
    assert frame["Riesgo Resultante"].tolist() == [
        "Riesgo Bajo",
        "Riesgo Alto",
        "Riesgo Bajo",
        "Riesgo Moderado",
        "Riesgo Máximo",
        "Riesgo Bajo",
    ]


# --- Basic queries on PSCV San José fixture ----------------------------------


def test_sanjose_query_cv_risk_low(fake_sanjose_path: Path) -> None:
    frame, _age, _sex = _load(fake_sanjose_path)
    # Ana, Bruno, Fabio
    assert _match_count(frame, "RCV = BAJO") == 3


def test_sanjose_query_diabetes_and_hba1c(fake_sanjose_path: Path) -> None:
    frame, _age, _sex = _load(fake_sanjose_path)
    # DM2 = SI: Ana, Bruno, Diego, Elena (4). Among them HbA1c < 8: Ana 7.0, Elena 6.5.
    assert _match_count(frame, "DIABETES = SI AND HBA1C < 8") == 2


def test_sanjose_query_hypertension_alias(fake_sanjose_path: Path) -> None:
    frame, _age, _sex = _load(fake_sanjose_path)
    # HTA = SI: Ana, Carla, Diego
    assert _match_count(frame, "HYPERTENSION") == 3


def test_sanjose_age_sex_table_for_low_risk(fake_sanjose_path: Path) -> None:
    frame, age_column, sex_column = _load(fake_sanjose_path)
    mask, _d, _label = build_expression_mask(frame, "RCV = BAJO", resolve_column)
    table = age_sex_count_table(
        frame.loc[mask], age_column=age_column, sex_column=sex_column
    )
    totals = totals_summary(table)

    assert totals["cell_total"] == 3
    assert totals["men_total"] == 2  # Bruno 72, Fabio 16
    assert totals["women_total"] == 1  # Ana 68
    assert int(table.loc["15 to 19 years", "Men"]) == 1
    assert int(table.loc["65 to 69 years", "Women"]) == 1
    assert int(table.loc["70 to 74 years", "Men"]) == 1


# --- Basic queries on diabetes-yeco fixture ----------------------------------


def test_diabetes_yeco_query_low_risk(fake_diabetes_yeco_path: Path) -> None:
    frame, _age, _sex = _load(fake_diabetes_yeco_path)
    # Alfa, Gamma, Zeta
    assert _match_count(frame, "RIESGO RESULTANTE = Riesgo Bajo") == 3


def test_diabetes_yeco_query_contains_riesgo(fake_diabetes_yeco_path: Path) -> None:
    frame, _age, _sex = _load(fake_diabetes_yeco_path)
    # Every row has "Riesgo" in the cell ("Riesgo Bajo", "Riesgo Alto", …).
    assert _match_count(frame, "RIESGO RESULTANTE *= Riesgo") == 6


def test_diabetes_yeco_age_sex_table_for_low_risk(
    fake_diabetes_yeco_path: Path,
) -> None:
    frame, age_column, sex_column = _load(fake_diabetes_yeco_path)
    mask, _d, _label = build_expression_mask(
        frame, "RIESGO RESULTANTE = Riesgo Bajo", resolve_column
    )
    table = age_sex_count_table(
        frame.loc[mask], age_column=age_column, sex_column=sex_column
    )
    totals = totals_summary(table)

    assert totals["cell_total"] == 3
    assert totals["women_total"] == 2  # Alfa 75, Gamma 68
    assert totals["men_total"] == 1  # Zeta 55
    assert int(table.loc["75 to 79 years", "Women"]) == 1
    assert int(table.loc["65 to 69 years", "Women"]) == 1
    assert int(table.loc["55 to 59 years", "Men"]) == 1


@pytest.mark.parametrize(
    "fixture_name,expected_skip",
    [
        ("fake_sanjose.csv", 3),
        ("fake_diabetes_yeco.csv", 7),
    ],
)
def test_load_without_manual_skiprows(fixture_name: str, expected_skip: int) -> None:
    """load_data must find the header on its own for both layouts."""
    path = Path(__file__).resolve().parent / "fixtures" / fixture_name
    assert _infer_csv_skiprows(path, ",") == expected_skip
    frame = load_data(path, separator=None, sheet=None).dropna(how="all")
    age_column, sex_column = resolve_age_sex_columns(frame)
    assert age_column
    assert sex_column
    assert len(frame) == 6
