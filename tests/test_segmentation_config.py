"""Tests for config/segmentation.conf and age-band derivation."""

from __future__ import annotations

from pathlib import Path

import pytest

from age_sex_segmentation import age_group_labels, assign_age_group
from interactive_query import batch_segmentation_header_lines
from segmentation_config import (
    clear_segmentation_cache,
    get_segmentation,
    parse_age_range,
    parse_segmentation_text,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_segmentation_cache()
    yield
    clear_segmentation_cache()


def test_default_segmentation_matches_historical_bands() -> None:
    cfg = get_segmentation()
    assert cfg.age_segmentation is True
    assert cfg.gender_segmentation is True
    assert cfg.age_range.start == 15
    assert cfg.age_range.step == 5
    assert cfg.age_range.open_from == 80
    labels = cfg.age_range.labels()
    assert labels[0] == "15 to 19 years"
    assert labels[-2] == "75 to 79 years"
    assert labels[-1] == "80 and over"
    assert len(labels) == 14
    assert "EDAD" in cfg.age_columns
    assert "Edad a la Aplicación" in cfg.age_columns
    assert "SEXO" in cfg.gender_columns
    assert "Sexo" in cfg.gender_columns


def test_parse_age_range_forms() -> None:
    assert parse_age_range("[15:5:80+]").open_from == 80
    assert parse_age_range("0:5:100+").start == 0
    with pytest.raises(ValueError, match="AGE_RANGE"):
        parse_age_range("15-80")


def test_assign_age_group_respects_configured_start() -> None:
    import pandas as pd

    text = """
    AGE_SEGMENTATION=true
    AGE_COLUMN=EDAD
    AGE_RANGE=[20:10:60+]
    GENDER_SEGMENTATION=false
    GENDER_COLUMN=SEXO
    """
    cfg = parse_segmentation_text(text)
    ages = pd.Series([15, 20, 25, 59, 60, 80])
    groups = assign_age_group(ages, config=cfg)
    assert pd.isna(groups.iloc[0])  # under start
    assert groups.iloc[1] == "20 to 29 years"
    assert groups.iloc[3] == "50 to 59 years"
    assert groups.iloc[4] == "60 and over"


def test_batch_segmentation_header_comments_default() -> None:
    age_line, sex_line = batch_segmentation_header_lines()
    assert age_line.startswith("# ")
    assert sex_line.startswith("# ")
    bands = age_line.removeprefix("# ").split("|")
    pairs = sex_line.removeprefix("# ").split("|")
    assert bands[0] == "15-19"
    assert bands[-2] == "75-79"
    assert bands[-1] == "80+"
    assert len(bands) == len(pairs) == 14
    assert all(pair == "M-F" for pair in pairs)


def test_custom_segmentation_file(tmp_path: Path) -> None:
    path = tmp_path / "segmentation.conf"
    path.write_text(
        """
        AGE_SEGMENTATION=true
        AGE_COLUMN=EDAD, "Edad custom"
        AGE_RANGE=[0:5:100+]
        GENDER_SEGMENTATION=true
        SEX_COLUMN=SEXO
        """,
        encoding="utf-8",
    )
    clear_segmentation_cache()
    cfg = get_segmentation(path)
    assert cfg.age_range.start == 0
    assert cfg.age_range.open_from == 100
    assert cfg.gender_columns == ("SEXO",)
    assert age_group_labels(config=cfg)[0] == "0 to 4 years"
    assert age_group_labels(config=cfg)[-1] == "100 and over"
