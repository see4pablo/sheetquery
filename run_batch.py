#!/usr/bin/env python3
"""Non-interactive runner: data file + queries JSON → output/batch_*.csv.

Intended for double-click use on Windows (SheetQuery.exe). With no
arguments it reads config/run.conf, then:
  - data from DATA_FILE, or auto-picks a spreadsheet under data/
  - queries from QUERIES_FILE (default queries/dani_y.json)
  - writes the batch file under output/

Examples:
  python run_batch.py
  python run_batch.py --data data/yeco.csv --queries queries/dani_y.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from app_config import get_app_config
from interactive_query import (
    DATA_DIR,
    OUTPUT_DIR,
    QUERIES_DIR,
    _load_query_catalog,
    _run_expression_list,
    resolve_data_path,
    resolve_queries_path,
)
from pscv_classification import load_data, resolve_age_sex_columns
from runtime_paths import ensure_app_workdir

DEFAULT_RUN_CONF = Path("config/run.conf")
BACKUP_RUN_CONF = Path("config/run.conf.backup")
DATA_GLOBS = ("*.csv", "*.tsv", "*.xlsx", "*.xls")


def _notify(title: str, message: str, *, error: bool = False) -> None:
    """Show a Windows message box when frozen/double-clicked; else print."""
    print(message)
    if sys.platform == "win32" and (
        getattr(sys, "frozen", False) or not sys.stdin.isatty()
    ):
        try:
            import ctypes

            flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(0, message, title, flags)
        except Exception:
            pass


def _pause_if_needed() -> None:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


def _parse_run_conf(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().upper()] = value.strip()
    return values


def _load_run_defaults() -> tuple[str, str]:
    for candidate in (DEFAULT_RUN_CONF, BACKUP_RUN_CONF):
        if candidate.is_file():
            values = _parse_run_conf(candidate)
            return values.get("DATA_FILE", ""), values.get("QUERIES_FILE", "")
    return "", "queries/dani_y.json"


def _auto_pick_data_file() -> Path | None:
    """Pick a spreadsheet under data/ (only one, or the newest)."""
    if not DATA_DIR.is_dir():
        return None
    found: list[Path] = []
    for pattern in DATA_GLOBS:
        found.extend(p for p in DATA_DIR.glob(pattern) if p.is_file())
    if not found:
        return None
    if len(found) == 1:
        return found[0].resolve()
    return max(found, key=lambda p: p.stat().st_mtime).resolve()


def _resolve_inputs(data_arg: str | None, queries_arg: str | None) -> tuple[Path, Path]:
    conf_data, conf_queries = _load_run_defaults()
    data_raw = (data_arg or conf_data or "").strip()
    queries_raw = (queries_arg or conf_queries or "queries/dani_y.json").strip()

    if data_raw:
        data_path = resolve_data_path(data_raw)
    else:
        picked = _auto_pick_data_file()
        if picked is None:
            raise FileNotFoundError(
                "No data file given and none found under data/.\n"
                "Put a .csv or .xlsx in the data/ folder, or set DATA_FILE in config/run.conf."
            )
        data_path = picked

    if not data_path.is_file():
        raise FileNotFoundError(f"Data file not found: {data_path}")

    queries_path = resolve_queries_path(queries_raw)
    if not queries_path.is_file():
        raise FileNotFoundError(
            f"Queries file not found: {queries_path}\n"
            f"Expected something like {QUERIES_DIR / 'dani_y.json'}."
        )
    return data_path, queries_path


def run_batch(data_path: Path, queries_path: Path) -> Path:
    """Execute all queries and return the batch output path."""
    expressions = _load_query_catalog(queries_path)
    if not expressions:
        raise ValueError(f"Queries file is empty: {queries_path}")

    frame = load_data(data_path, separator=None, sheet=None).dropna(how="all")
    age_column, sex_column = resolve_age_sex_columns(frame)

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_path = output_dir / f"batch_{data_path.stem}_{stamp}.csv"

    print(f"Data:    {data_path}  ({len(frame)} rows)")
    print(f"Queries: {queries_path}  ({len(expressions)} expressions)")
    print(f"Age: {age_column} · Sex: {sex_column}")
    if get_app_config().silent_mode:
        print("Silent mode ON — only the batch file will be written.\n")
    else:
        print("Silent mode OFF — also writing per-query CSV files.\n")

    _run_expression_list(
        expressions=expressions,
        frame=frame,
        source=data_path,
        output_dir=output_dir,
        batch_path=batch_path,
        stamp=stamp,
        age_column=age_column,
        sex_column=sex_column,
    )
    if not batch_path.is_file():
        raise RuntimeError(
            "No batch file was produced (all rows were blank separators?)."
        )
    return batch_path.resolve()


def main(argv: list[str] | None = None) -> int:
    ensure_app_workdir()
    parser = argparse.ArgumentParser(
        description="Run a queries JSON against a PSCV/CESFAM spreadsheet (no prompts).",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Spreadsheet path (default: config/run.conf DATA_FILE, or auto data/)",
    )
    parser.add_argument(
        "--queries",
        default=None,
        help="Queries JSON path (default: config/run.conf QUERIES_FILE)",
    )
    args = parser.parse_args(argv)

    try:
        data_path, queries_path = _resolve_inputs(args.data, args.queries)
        batch_path = run_batch(data_path, queries_path)
    except Exception as exc:
        _notify("SheetQuery — error", str(exc), error=True)
        _pause_if_needed()
        return 1

    message = (
        "Finished successfully.\n\n"
        f"Batch file:\n{batch_path}\n\n"
        "You can open that file with Excel."
    )
    print(f"\nDone. Batch file: {batch_path}")
    _notify("SheetQuery — done", message, error=False)
    _pause_if_needed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
