"""In-browser / in-memory SheetQuery API (Pyodide-friendly).

Used by see4pablo-mainpage: the page fetches this module (and its siblings)
from the sheetquery GitHub repo, then calls ``run_batch_csv`` so clinic files
never leave the user's machine.

Do not import interactive_query or run_batch here (readline / Path / Windows UI).
"""

from __future__ import annotations

import contextlib
import csv
import io
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from column_aliases import (
    _alias_key,
    _collapse_spaces,
    clear_alias_cache,
    column_fingerprint,
    get_aliases,
    pick_column_from_candidates,
)
from pscv_classification import (
    is_age_column,
    is_sex_column,
    resolve_age_sex_columns,
)
from query_conditions import build_expression_mask, sanitize_csv_comment
from segmentation_config import (
    clear_segmentation_cache,
    get_segmentation,
)
from segmented_query import run_query_on_mask

BLANK_COMMANDS = frozenset({"BLANK", "SPACE", "ESPACIO"})

_CONFIG_DIR = Path("config")
_ALIASES_PATH = _CONFIG_DIR / "column_aliases.conf"
_SEGMENTATION_PATH = _CONFIG_DIR / "segmentation.conf"


def configure(*, aliases_text: str, segmentation_text: str) -> None:
    """Write conf files into the virtual FS and refresh caches."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _ALIASES_PATH.write_text(aliases_text, encoding="utf-8")
    _SEGMENTATION_PATH.write_text(segmentation_text, encoding="utf-8")
    clear_alias_cache()
    clear_segmentation_cache()
    # Warm caches so later calls hit the files we just wrote.
    get_aliases()
    get_segmentation()


def resolve_column(df: pd.DataFrame, name_or_fragment: str) -> str:
    """Resolve alias / header; raise if several substring matches exist."""
    name = _collapse_spaces(name_or_fragment)
    key = _alias_key(name)
    aliases = get_aliases()

    if key in aliases:
        picked = pick_column_from_candidates(df, aliases[key])
        if picked is not None:
            return picked
        expected = ", ".join(repr(header) for header in aliases[key])
        raise ValueError(
            f"No column found for alias {key!r}. Expected one of: {expected}"
        )

    all_candidates = [header for headers in aliases.values() for header in headers]
    if column_fingerprint(name) in {
        column_fingerprint(header) for header in all_candidates
    }:
        picked = pick_column_from_candidates(df, [name, *all_candidates])
        if picked is not None:
            return picked

    columns = list(df.columns)
    if name in columns:
        return name

    collapsed = {_collapse_spaces(str(column)): str(column) for column in columns}
    if name in collapsed:
        return collapsed[name]

    lowered = name.lower()
    case_matches = [
        column for column in columns if str(column).strip().lower() == lowered
    ]
    if len(case_matches) == 1:
        return str(case_matches[0])

    candidates = [column for column in columns if lowered in str(column).lower()]
    if len(candidates) == 1:
        return str(candidates[0])
    if not candidates:
        raise ValueError(f"No column matches {name!r}.")
    listed = "; ".join(repr(str(c)) for c in candidates[:12])
    raise ValueError(
        f"Several columns match {name!r}: {listed}. "
        "Use a more specific name or an alias from column_aliases.conf."
    )


def _infer_csv_skiprows_text(csv_text: str, separator: str = ",") -> int:
    cfg = get_segmentation()
    buffer = io.StringIO(csv_text)
    for skip in range(0, 35):
        buffer.seek(0)
        try:
            head = pd.read_csv(
                buffer,
                sep=separator,
                encoding="utf-8-sig",
                skiprows=skip,
                nrows=0,
                dtype=str,
            )
        except (pd.errors.ParserError, UnicodeDecodeError, ValueError):
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


def load_csv_text(csv_text: str, *, separator: str = ",") -> pd.DataFrame:
    """Load a CSV string (utf-8 / utf-8-sig), detecting title rows above headers."""
    skip = _infer_csv_skiprows_text(csv_text, separator)
    frame = pd.read_csv(
        io.StringIO(csv_text),
        sep=separator,
        dtype=str,
        encoding="utf-8-sig",
        skiprows=skip,
    )
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame.dropna(how="all")


def _compact_age_band_token(label: str) -> str:
    cleaned = re.sub(r"\s+years?$", "", label.strip(), flags=re.I)
    cleaned = re.sub(r"\s+and over$", "+", cleaned, flags=re.I)
    if " to " in cleaned:
        low, high = cleaned.split(" to ", 1)
        return f"{low.strip()}-{high.strip()}"
    return cleaned.replace(" ", "-")


def _csv_line(cells: list[object]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(cells)
    return buf.getvalue()


def batch_named_header_rows() -> list[list[str]]:
    """Two header rows: Age + band names (each twice), then Query + M,F pairs."""
    cfg = get_segmentation()
    if cfg.age_segmentation and cfg.gender_segmentation:
        bands = [_compact_age_band_token(label) for label in cfg.age_range.labels()]
        age_row: list[str] = ["Age"]
        sex_row: list[str] = ["Query"]
        for band in bands:
            age_row.extend([band, band])
            sex_row.extend(["M", "F"])
        return [age_row, sex_row]
    if cfg.age_segmentation:
        bands = [_compact_age_band_token(label) for label in cfg.age_range.labels()]
        return [["Age", *bands], ["Query", *(["Count"] * len(bands))]]
    if cfg.gender_segmentation:
        return [["Age", "All"], ["Query", "M", "F"]]
    return [["Age"], ["Query", "Count"]]


def table_to_batch_values(table: pd.DataFrame) -> list[int]:
    row = table.iloc[0]
    columns = table.columns
    if not isinstance(columns, pd.MultiIndex):
        raise ValueError("Batch table expected a MultiIndex (age group × sex).")
    values: list[int] = []
    index = 0
    width = len(columns)
    while index < width:
        group, sex = columns[index]
        if str(group).strip() == "Total":
            break
        if sex != "Men" or index + 1 >= width:
            raise ValueError("Unexpected column layout in the batch table.")
        _group, women = columns[index + 1]
        if women != "Women":
            raise ValueError("Unexpected column layout in the batch table.")
        values.append(int(row.iloc[index]))
        values.append(int(row.iloc[index + 1]))
        index += 2
    return values


def table_to_batch_number_line(table: pd.DataFrame) -> str:
    """Legacy helper: comma-separated counts only."""
    return ", ".join(str(value) for value in table_to_batch_values(table))


def run_batch_csv(
    csv_text: str,
    queries: list[str],
    *,
    source_name: str = "upload.csv",
    aliases_text: str | None = None,
    segmentation_text: str | None = None,
) -> str:
    """Filter ``csv_text`` with ``queries``; return batch CSV text (comments + rows).

    Blank commands: BLANK / SPACE / ESPACIO → empty separator line.
    """
    if aliases_text is not None and segmentation_text is not None:
        configure(aliases_text=aliases_text, segmentation_text=segmentation_text)
    elif not _ALIASES_PATH.is_file() or not _SEGMENTATION_PATH.is_file():
        raise FileNotFoundError(
            "Missing config. Pass aliases_text and segmentation_text, "
            "or write config/column_aliases.conf and config/segmentation.conf first."
        )

    frame = load_csv_text(csv_text)
    if frame.empty:
        raise ValueError("Spreadsheet is empty after load.")
    age_column, sex_column = resolve_age_sex_columns(frame)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lines = [
        f"# File: {sanitize_csv_comment(source_name)}",
        f"# Session: {stamp}",
    ]
    for header_row in batch_named_header_rows():
        lines.append(_csv_line(header_row))

    for raw in queries:
        phrase = (raw or "").strip()
        if not phrase:
            continue
        if phrase.upper() in BLANK_COMMANDS:
            lines.append("")
            continue
        mask, _descriptions, label = build_expression_mask(frame, phrase, resolve_column)
        with contextlib.redirect_stdout(io.StringIO()):
            table = run_query_on_mask(
                frame,
                mask,
                condition_description=f"Expression: {phrase}",
                age_column=age_column,
                sex_column=sex_column,
                include_totals=True,
                short_label=label,
            )
        lines.append(_csv_line([phrase, *table_to_batch_values(table)]))

    return "\n".join(lines) + "\n"
