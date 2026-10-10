"""Tests for non-interactive path resolution (run_batch defaults)."""

from __future__ import annotations

from pathlib import Path

import pytest

from run_batch import _auto_pick_data_file, _parse_run_conf, _resolve_inputs


def test_parse_run_conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "run.conf"
    conf.write_text(
        "DATA_FILE=data/demo.csv\nQUERIES_FILE=queries/dani_y.json\n",
        encoding="utf-8",
    )
    values = _parse_run_conf(conf)
    assert values["DATA_FILE"] == "data/demo.csv"
    assert values["QUERIES_FILE"] == "queries/dani_y.json"


def test_auto_pick_newest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    older = data / "a.csv"
    newer = data / "b.csv"
    older.write_text("x\n", encoding="utf-8")
    newer.write_text("y\n", encoding="utf-8")
    # Ensure mtime order
    import os
    import time

    os.utime(older, (time.time() - 100, time.time() - 100))
    os.utime(newer, None)

    monkeypatch.chdir(tmp_path)
    import run_batch as rb

    monkeypatch.setattr(rb, "DATA_DIR", Path("data"))
    picked = rb._auto_pick_data_file()
    assert picked is not None
    assert picked.name == "b.csv"


def test_resolve_inputs_uses_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "queries").mkdir()
    (tmp_path / "config").mkdir()
    data_file = tmp_path / "data" / "demo.csv"
    data_file.write_text("EDAD,SEXO\n20,F\n", encoding="utf-8")
    queries = tmp_path / "queries" / "dani_y.json"
    queries.write_text('{"queries": ["SEXO = F"]}\n', encoding="utf-8")
    (tmp_path / "config" / "run.conf").write_text(
        "DATA_FILE=data/demo.csv\nQUERIES_FILE=queries/dani_y.json\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    import run_batch as rb

    monkeypatch.setattr(rb, "DEFAULT_RUN_CONF", Path("config/run.conf"))
    monkeypatch.setattr(rb, "BACKUP_RUN_CONF", Path("config/run.conf.backup"))
    monkeypatch.setattr(rb, "DATA_DIR", Path("data"))
    monkeypatch.setattr(rb, "QUERIES_DIR", Path("queries"))

    data_path, queries_path = rb._resolve_inputs(None, None)
    assert data_path.name == "demo.csv"
    assert queries_path.name == "dani_y.json"
