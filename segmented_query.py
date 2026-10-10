#!/usr/bin/env python3
"""Run one PSCV filter and print the result by age band and sex.

Example (low cardiovascular risk):
  python segmented_query.py data/file.tsv --preset low-risk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from age_sex_segmentation import (
    filter_column_value,
    prepare_dimensions,
    totals_summary,
    wide_age_sex_men_women_table,
    age_sex_count_table,
)
from pscv_classification import load_data, resolve_age_sex_columns
from pscv_columns import COL_CV_RISK
from query_conditions import label_from_column_value_mode, sanitize_csv_comment
from segmentation_config import get_segmentation

PRESETS: dict[str, dict[str, str]] = {
    "low-risk": {
        "column": COL_CV_RISK,
        "value": "BAJO",
        "mode": "normalized_equals",
        "title": "Patients with cardiovascular risk classified as LOW",
        "label": "CV risk = BAJO",
    },
    "moderate-risk": {
        "column": COL_CV_RISK,
        "value": "MODERADO",
        "mode": "normalized_equals",
        "title": "Patients with cardiovascular risk classified as MODERATE",
        "label": "CV risk = MODERADO",
    },
    "high-risk": {
        "column": COL_CV_RISK,
        "value": "ALTO",
        "mode": "normalized_equals",
        "title": "Patients with cardiovascular risk classified as HIGH",
        "label": "CV risk = ALTO",
    },
}


def run_query_on_mask(
    df: pd.DataFrame,
    mask: pd.Series,
    *,
    condition_description: str,
    age_column: str,
    sex_column: str,
    include_totals: bool,
    short_label: str | None = None,
) -> pd.DataFrame:
    """Count matching rows by age band and sex. Returns the wide one-row table."""
    subset = df.loc[mask]
    if short_label:
        rule = "─" * min(76, max(24, len(short_label) + 12))
        print(f"\n{rule}\n  Query: {short_label}\n{rule}")
    print(f"\n{condition_description}")
    print(f"Patients matching every condition: {len(subset)}")

    cfg = get_segmentation()
    prepared = prepare_dimensions(subset, age_column=age_column, sex_column=sex_column)
    if cfg.age_segmentation and "_age_group" in prepared.columns:
        missing_group = int(prepared["_age_group"].isna().sum())
        if missing_group:
            print(
                f"Notice: {missing_group} record(s) have no age band "
                f"(age missing or under {cfg.age_range.start}) and are left out of the table.",
                file=sys.stderr,
            )

    long_table = age_sex_count_table(
        subset, age_column=age_column, sex_column=sex_column
    )
    totals = totals_summary(long_table)
    wide = wide_age_sex_men_women_table(long_table, include_grand_totals=include_totals)
    parts = [f"Rows in the table: {totals['cell_total']}"]
    if cfg.age_segmentation:
        parts[0] = (
            f"Rows in the table (age ≥ {cfg.age_range.start} with a band): "
            f"{totals['cell_total']}"
        )
    if "men_total" in totals:
        parts.append(f"Men {totals['men_total']}")
    if "women_total" in totals:
        parts.append(f"Women {totals['women_total']}")
    if "other_sex_total" in totals:
        parts.append(f"other sex {totals['other_sex_total']}")
    print(parts[0] + ((" (" + ", ".join(parts[1:]) + ")") if len(parts) > 1 else ""))
    return wide


def run_query(
    df: pd.DataFrame,
    *,
    column: str,
    value: str,
    mode: str,
    age_column: str,
    sex_column: str,
    title: str,
    include_totals: bool,
    short_label: str | None = None,
) -> pd.DataFrame:
    """Filter one column, then build the age-by-sex table."""
    if column not in df.columns:
        raise SystemExit(f"Column not found: {column!r}")
    mask = filter_column_value(df, column, value, mode=mode)
    description = (
        f"{title}\nCondition: column {column!r}, value {value!r} (mode {mode})"
    )
    label = short_label or label_from_column_value_mode(column, value, mode)
    return run_query_on_mask(
        df,
        mask,
        condition_description=description,
        age_column=age_column,
        sex_column=sex_column,
        include_totals=include_totals,
        short_label=label,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PSCV query segmented by age band (5-year groups, 15–80+) and sex",
    )
    parser.add_argument(
        "file", type=Path, nargs="?", default=Path("data/datos_ejemplo.tsv")
    )
    parser.add_argument("--sep", default=None)
    parser.add_argument("--sheet", default=None)
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        help="Built-in query (for example low-risk)",
    )
    parser.add_argument("--column", default=None, help="Exact column header to filter")
    parser.add_argument("--value", default=None, help="Text value to compare")
    parser.add_argument(
        "--mode",
        choices=("normalized_equals", "contains"),
        default="normalized_equals",
        help="normalized_equals: whole cell; contains: substring",
    )
    parser.add_argument(
        "--age-column",
        default=None,
        help="Age header (default: first AGE_COLUMN match from segmentation.conf)",
    )
    parser.add_argument(
        "--sex-column",
        default=None,
        help="Sex header (default: first GENDER_COLUMN match from segmentation.conf)",
    )
    parser.add_argument(
        "--no-totals",
        action="store_true",
        help="Omit the Total / Men and Total / Women columns",
    )
    parser.add_argument(
        "--csv", type=Path, default=None, help="Also write the table to this CSV"
    )
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    frame = load_data(args.file, args.sep, args.sheet)
    age_column = args.age_column
    sex_column = args.sex_column
    if (
        age_column is None
        or sex_column is None
        or age_column not in frame.columns
        or sex_column not in frame.columns
    ):
        try:
            detected_age, detected_sex = resolve_age_sex_columns(frame)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        if age_column is None or age_column not in frame.columns:
            age_column = detected_age
        if sex_column is None or sex_column not in frame.columns:
            sex_column = detected_sex

    preset_label: str | None = None
    if args.preset:
        preset = PRESETS[args.preset]
        column = preset["column"]
        value = preset["value"]
        mode = preset["mode"]
        title = preset["title"]
        preset_label = preset.get("label")
    else:
        if not args.column or args.value is None:
            parser.error("Pass --preset, or both --column and --value")
        column = args.column
        value = args.value
        mode = args.mode
        title = f"Patients with {column} = {value}"

    table = run_query(
        frame,
        column=column,
        value=value,
        mode=mode,
        age_column=age_column,
        sex_column=sex_column,
        title=title,
        include_totals=not args.no_totals,
        short_label=preset_label,
    )

    label = preset_label or label_from_column_value_mode(column, value, mode)
    pd.set_option("display.width", 120)
    pd.set_option("display.max_rows", 30)
    print(f"\n--- Age and sex table · {label} ---\n")
    print(table.to_string())

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8-sig", newline="") as handle:
            handle.write(f"# Query: {sanitize_csv_comment(label)}\n")
        table.to_csv(args.csv, mode="a", index=False)
        print(f"\nCSV: {args.csv.resolve()}")


if __name__ == "__main__":
    main()
