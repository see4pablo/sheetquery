"""Patient deduplication by DEDUP_COLUMN (Documento / RUT)."""

from __future__ import annotations

import pandas as pd
import pytest

from pscv_classification import deduplicate_by_column, resolve_dedup_column
from segmentation_config import clear_segmentation_cache, get_segmentation, parse_segmentation_text
from segmented_query import run_query_on_mask


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_segmentation_cache()
    yield
    clear_segmentation_cache()


def test_deduplicate_keeps_first_row_per_documento() -> None:
    frame = pd.DataFrame(
        {
            "Documento": ["1-1", "1-1", "2-2", "", "2-2"],
            "EDAD": ["40", "41", "50", "30", "51"],
            "SEXO": ["M", "M", "F", "F", "F"],
        }
    )
    out = deduplicate_by_column(frame, "Documento")
    assert list(out["Documento"]) == ["1-1", "2-2", ""]
    assert list(out["EDAD"]) == ["40", "50", "30"]


def test_resolve_dedup_documento_or_rut(tmp_path, monkeypatch) -> None:
    conf = tmp_path / "segmentation.conf"
    conf.write_text(
        """
        AGE_SEGMENTATION=true
        AGE_COLUMN=EDAD
        AGE_RANGE=[15:5:80+]
        GENDER_SEGMENTATION=true
        GENDER_COLUMN=SEXO
        DEDUP_COLUMN=Documento, RUT
        """,
        encoding="utf-8",
    )
    cfg = get_segmentation(conf)
    monkeypatch.setattr(
        "segmentation_config.get_segmentation", lambda path=None: cfg
    )

    frame = pd.DataFrame(
        {"Documento": ["1"], "RUT": ["9"], "EDAD": ["20"], "SEXO": ["M"]}
    )
    assert resolve_dedup_column(frame) == "Documento"

    frame_rut = pd.DataFrame({"RUT": ["9"], "EDAD": ["20"], "SEXO": ["M"]})
    assert resolve_dedup_column(frame_rut) == "RUT"


def test_run_query_on_mask_dedups_before_count(capsys, tmp_path, monkeypatch) -> None:
    conf = tmp_path / "segmentation.conf"
    conf.write_text(
        """
        AGE_SEGMENTATION=true
        AGE_COLUMN=EDAD
        AGE_RANGE=[15:5:80+]
        GENDER_SEGMENTATION=true
        GENDER_COLUMN=SEXO
        DEDUP_COLUMN=Documento
        """,
        encoding="utf-8",
    )
    cfg = get_segmentation(conf)
    monkeypatch.setattr("segmentation_config.get_segmentation", lambda path=None: cfg)
    monkeypatch.setattr("segmented_query.get_segmentation", lambda path=None: cfg)
    monkeypatch.setattr("age_sex_segmentation.get_segmentation", lambda path=None: cfg)

    frame = pd.DataFrame(
        {
            "Documento": ["A", "A", "B"],
            "EDAD": ["68", "68", "72"],
            "SEXO": ["FEMENINO", "FEMENINO", "MASCULINO"],
        }
    )
    mask = pd.Series([True, True, True])
    table = run_query_on_mask(
        frame,
        mask,
        condition_description="all",
        age_column="EDAD",
        sex_column="SEXO",
        include_totals=True,
    )
    captured = capsys.readouterr().out
    assert "unique by Documento" in captured
    assert "Patients matching every condition: 2" in captured
    assert "1 duplicate removed from 3 rows" in captured
    value_cols = [
        col for col in table.columns if str(col[0]).strip() != "Total"
    ]
    assert int(table[value_cols].iloc[0].sum()) == 2


def test_empty_dedup_column_disables() -> None:
    cfg = parse_segmentation_text(
        """
        AGE_SEGMENTATION=true
        AGE_COLUMN=EDAD
        AGE_RANGE=[15:5:80+]
        GENDER_SEGMENTATION=true
        GENDER_COLUMN=SEXO
        DEDUP_COLUMN=
        """
    )
    assert cfg.dedup_columns == ()
