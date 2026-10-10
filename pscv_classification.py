#!/usr/bin/env python3
"""Load a PSCV/CESFAM workbook, summarize qualitative columns, and save a selection.

The saved JSON lists the columns that should act as classification dimensions.
Run it once when a new export arrives and the headers need to be reviewed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_CONFIG = Path("config/classification_columns.json")
YES_NO_HINT = re.compile(r"\bSI\b|\bNO\b|SI/NO|SI, NO", re.I)


def _normalize_column_name(name: str) -> str:
    """Uppercase, strip accents, and collapse whitespace."""
    text = unicodedata.normalize("NFKD", str(name).strip())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).upper()


def is_age_column(name: str) -> bool:
    """True when `name` matches a configured AGE_COLUMN candidate.

    Also accepts headers that start with a short candidate such as EDAD
    (e.g. "Edad a la Aplicación") so skiprow detection stays robust.
    """
    from column_aliases import column_fingerprint
    from segmentation_config import get_segmentation

    cfg = get_segmentation()
    fingerprint = column_fingerprint(name)
    for candidate in cfg.age_columns:
        candidate_fp = column_fingerprint(candidate)
        if fingerprint == candidate_fp:
            return True
        # Short tokens like EDAD also match "EDAD A LA APLICACION".
        if " " not in candidate_fp and (
            fingerprint == candidate_fp or fingerprint.startswith(candidate_fp)
        ):
            return True
    # Legacy fallback when config lists EDAD-style names.
    normalized = _normalize_column_name(name)
    return normalized == "EDAD" or normalized.startswith("EDAD ")


def is_sex_column(name: str) -> bool:
    """True when `name` matches a configured GENDER_COLUMN candidate."""
    from column_aliases import column_fingerprint
    from segmentation_config import get_segmentation

    cfg = get_segmentation()
    fingerprint = column_fingerprint(name)
    for candidate in cfg.gender_columns:
        candidate_fp = column_fingerprint(candidate)
        if fingerprint == candidate_fp:
            return True
        if " " not in candidate_fp and (
            fingerprint == candidate_fp or fingerprint.startswith(candidate_fp)
        ):
            return True
    normalized = _normalize_column_name(name)
    return normalized == "SEXO" or normalized.startswith("SEXO ")


def resolve_age_sex_columns(df: pd.DataFrame) -> tuple[str, str]:
    """Return the real age and sex header names for the active segmentation config."""
    from column_aliases import pick_column_from_candidates
    from segmentation_config import get_segmentation

    cfg = get_segmentation()
    age = pick_column_from_candidates(df, list(cfg.age_columns))
    sex = pick_column_from_candidates(df, list(cfg.gender_columns))
    if age is None:
        age = next(
            (str(column) for column in df.columns if is_age_column(str(column))), None
        )
    if sex is None:
        sex = next(
            (str(column) for column in df.columns if is_sex_column(str(column))), None
        )
    missing: list[str] = []
    if cfg.age_segmentation and age is None:
        missing.append("age (" + ", ".join(repr(c) for c in cfg.age_columns) + ")")
    if cfg.gender_segmentation and sex is None:
        missing.append("sex (" + ", ".join(repr(c) for c in cfg.gender_columns) + ")")
    if missing:
        raise ValueError("Missing column for " + " and ".join(missing) + ".")
    if age is None or sex is None:
        # Callers that still unpack two strings get a clear failure if a flag is off.
        raise ValueError(
            "Age and sex columns are both required by this caller; "
            "enable AGE_SEGMENTATION and GENDER_SEGMENTATION or update the caller."
        )
    return age, sex


def _infer_csv_skiprows(path: Path, separator: str) -> int:
    """Find the real header row.

    Some exports (yeco.csv, diabetes-yeco.csv) put a title block above the
    column names. Scan the first rows until the configured age/sex headers appear.
    """
    from segmentation_config import get_segmentation

    cfg = get_segmentation()
    for skip in range(0, 35):
        try:
            head = pd.read_csv(
                path,
                sep=separator,
                encoding="utf-8-sig",
                skiprows=skip,
                nrows=0,
                dtype=str,
            )
        except (pd.errors.ParserError, UnicodeDecodeError, OSError):
            continue
        columns = [str(column).strip() for column in head.columns]
        age_ok = (not cfg.age_segmentation) or any(
            is_age_column(column) for column in columns
        )
        sex_ok = (not cfg.gender_segmentation) or any(
            is_sex_column(column) for column in columns
        )
        if age_ok and sex_ok:
            return skip
    return 0


def load_data(
    path: Path,
    separator: str | None,
    sheet: str | None,
    skiprows: int | None = None,
    detect_csv_header: bool = True,
) -> pd.DataFrame:
    """Load a TSV, CSV, or Excel workbook. Every cell is kept as text.

    Text dtype avoids pandas turning clinical codes and RUN-like values into
    numbers and dropping leading zeros or check digits.
    """
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        frame = pd.read_excel(path, sheet_name=sheet or 0, dtype=str)
        frame.columns = [str(column).strip() for column in frame.columns]
        return frame
    if separator is None:
        separator = "\t" if suffix == ".tsv" else ","
    if skiprows is None and detect_csv_header:
        skiprows = _infer_csv_skiprows(path, separator)
    elif skiprows is None:
        skiprows = 0
    frame = pd.read_csv(
        path,
        sep=separator,
        dtype=str,
        encoding="utf-8-sig",
        skiprows=skiprows,
    )
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def qualitative_summary(df: pd.DataFrame, max_categories: int = 25) -> pd.DataFrame:
    """One row per column: non-empty count, unique count, and a value sample.

    A column looks like yes/no when its header says SI/NO, or when the
    distinct values are only SI, NO, and a few 'unknown' tokens.
    """
    rows: list[dict[str, Any]] = []
    for column in df.columns:
        series = df[column]
        filled = series.notna() & (series.astype(str).str.strip() != "")
        valid_count = int(filled.sum())
        values = series[filled].astype(str).str.strip()
        unique_count = values.nunique()
        sample = (
            values.value_counts().head(8).to_dict()
            if unique_count <= max_categories
            else {}
        )
        looks_yes_no = bool(YES_NO_HINT.search(str(column))) or (
            unique_count <= 6
            and valid_count > 0
            and set(values.str.upper().unique())
            <= {"SI", "NO", "NO APLICA", "DESCONOCE", "NAN", ""}
        )
        rows.append(
            {
                "column": column,
                "non_empty": valid_count,
                "n_unique": int(unique_count),
                "looks_yes_no": looks_yes_no,
                "value_sample": sample,
            }
        )
    return pd.DataFrame(rows)


def print_summary(summary: pd.DataFrame) -> None:
    pd.set_option("display.max_rows", 200)
    pd.set_option("display.max_colwidth", 60)
    print("\n--- Per-column summary (sample shown when there are few categories) ---\n")
    for _, row in summary.iterrows():
        flag = " [possible YES/NO]" if row["looks_yes_no"] else ""
        print(f"{row['column']}{flag}")
        print(f"   non-empty: {row['non_empty']}, distinct values: {row['n_unique']}")
        if row["value_sample"]:
            for key, count in list(row["value_sample"].items())[:6]:
                print(f"      {key!r}: {count}")
        print()


def suggested_classification_columns(summary: pd.DataFrame) -> list[str]:
    """Yes/no columns, plus any column with at most 15 distinct values."""
    chosen: list[str] = []
    for _, row in summary.iterrows():
        column = str(row["column"])
        if row["looks_yes_no"] or (row["n_unique"] <= 15 and row["non_empty"] > 0):
            chosen.append(column)
    return chosen


def prompt_column_selection(df: pd.DataFrame, suggested: list[str]) -> list[str]:
    """Ask which columns belong in the classification. Empty input selects none."""
    columns = list(df.columns)
    print("\n--- Columns in the file ---\n")
    for index, column in enumerate(columns, start=1):
        mark = " *" if column in suggested else ""
        print(f"  {index:3d}. {column}{mark}")
    print("\n(*) suggested qualitative / YES-NO columns (you can pick others).\n")
    print(
        "Which columns go into the CLASSIFICATION?\n"
        "Format: comma-separated numbers (e.g. 7,14,15,24) or exact names.\n"
        "Commands: 'suggested' (starred only), 'all', 'none'.\n"
        "Spanish aliases still work: sugeridas, todas, vacio, ninguna.\n"
    )
    raw = input("Your selection: ").strip()
    lowered = raw.lower()
    if not raw or lowered in ("none", "vacío", "vacio", "ninguna"):
        return []
    if lowered in ("all", "todas"):
        return columns
    if lowered in ("suggested", "sugeridas"):
        return [column for column in columns if column in suggested]

    picked: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            index = int(token)
            if 1 <= index <= len(columns):
                picked.append(columns[index - 1])
            else:
                print(f"  (ignored out-of-range index: {index})", file=sys.stderr)
        elif token in columns:
            picked.append(token)
        else:
            case_match = [
                column for column in columns if column.lower() == token.lower()
            ]
            if case_match:
                picked.append(case_match[0])
            else:
                print(f"  (column not found: {token})", file=sys.stderr)
    seen: set[str] = set()
    ordered: list[str] = []
    for column in picked:
        if column not in seen:
            seen.add(column)
            ordered.append(column)
    return ordered


def save_config(columns: list[str], path: Path, data_source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "classification_columns": columns,
        "data_source": data_source,
        "note": "These columns are the qualitative dimensions / classification rules.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {path.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PSCV: load a workbook and choose classification columns"
    )
    parser.add_argument(
        "file", type=Path, nargs="?", default=Path("data/datos_ejemplo.tsv")
    )
    parser.add_argument(
        "--sep",
        default=None,
        help="CSV separator (default: tab for .tsv, comma for .csv)",
    )
    parser.add_argument("--sheet", default=None, help="Excel sheet name")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Output JSON path for the chosen columns",
    )
    parser.add_argument(
        "--non-interactive", action="store_true", help="Print the summary and exit"
    )
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    frame = load_data(args.file, args.sep, args.sheet)
    print(f"Loaded: {args.file} — {len(frame)} rows, {len(frame.columns)} columns.")

    summary = qualitative_summary(frame)
    print_summary(summary)

    suggested = suggested_classification_columns(summary)
    print(f"Suggested classification columns ({len(suggested)}): ")
    for column in suggested:
        print(f"  - {column}")

    if args.non_interactive:
        return

    chosen = prompt_column_selection(frame, suggested)
    print("\n--- Columns chosen for classification ---")
    for column in chosen:
        print(f"  • {column}")
    save_config(chosen, args.config, str(args.file.resolve()))


if __name__ == "__main__":
    main()
