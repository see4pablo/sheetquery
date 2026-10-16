"""In-memory batch API used by the Pyodide web UI."""

from __future__ import annotations

from pathlib import Path

from browser_api import run_batch_csv

ROOT = Path(__file__).resolve().parents[1]


def test_run_batch_csv_toy_english_headers(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    aliases = (ROOT / "config" / "column_aliases.conf").read_text(encoding="utf-8")
    aliases += "\nDiabetes=Diabetes\nSmoker=Smoker\n"
    segmentation = (ROOT / "config" / "segmentation.conf").read_text(encoding="utf-8")
    segmentation = (
        segmentation.replace(
            'AGE_COLUMN=EDAD, "Edad a la Aplicación"',
            'AGE_COLUMN=EDAD, "Edad a la Aplicación", Age',
        )
        .replace(
            "GENDER_COLUMN=SEXO, Sexo",
            "GENDER_COLUMN=SEXO, Sexo, Sex",
        )
        .replace(
            "DEDUP_COLUMN=Documento, RUT, Columna 3",
            "DEDUP_COLUMN=",
        )
    )

    csv_text = """Age,Sex,Diabetes,Smoker
42,M,YES,NO
43,F,YES,YES
41,M,NO,NO
55,F,YES,NO
"""
    out = run_batch_csv(
        csv_text,
        ["Diabetes = YES", "SPACE", "Diabetes = YES AND Smoker = YES"],
        source_name="demo.csv",
        aliases_text=aliases,
        segmentation_text=segmentation,
    )
    assert "# File: demo.csv" in out
    assert "M-F" in out
    lines = [line for line in out.splitlines() if not line.startswith("#")]
    assert len(lines) >= 3
    assert lines[1] == ""
    assert "," in lines[0]
    assert "," in lines[2]
