#!/usr/bin/env python3
"""Interactive PSCV queries: pick a data file, optional queries file, run filters.

Flow:
  1. Data workbook (looked up under data/ when relative).
  2. Optional queries JSON (looked up under queries/). Empty Enter skips it.
  3. If a queries file was given, those expressions are run and the session ends.
  4. If not, expressions are typed interactively. When you finish, the session
     expressions are saved to a new file under queries/ (queries_<stamp>.json).

Each successful query appends one line to output/batch_*.csv. Per-query CSVs
depend on SILENT_MODE in config/app.conf (default: silent, batch only).

Spreadsheet values stay in Spanish (SI, NO, BAJO, the real headers).
The words you type may be English or the older Spanish forms:

  CURRENT or VIGENTE     date of that control is within the last 12 months
  HYPERTENSION           hypertension diagnosis = SI
  CKD or ERC             ACR >= 30 OR eGFR < 60
  BLANK, SPACE, or ESPACIO   blank separator row in the batch file
  PREVIOUS or ANTERIOR       undo the last batch line (and its CSV, if any)
"""

from __future__ import annotations

import json
import re
import readline
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from app_config import get_app_config
from column_aliases import resolve_column
from pscv_classification import load_data, resolve_age_sex_columns, resolve_dedup_column
from pscv_columns import COL_CV_RISK, COL_DIABETES_MELLITUS_2, COL_HBA1C_VALUE
from query_conditions import (
    build_expression_mask,
    sanitize_csv_comment,
    slug_for_filename,
)
from runtime_paths import ensure_app_workdir
from segmentation_config import SegmentationConfig, get_segmentation
from segmented_query import run_query_on_mask

DATA_DIR = Path("data")
QUERIES_DIR = Path("queries")
OUTPUT_DIR = Path("output")

BLANK_COMMANDS = frozenset({"BLANK", "SPACE", "ESPACIO"})
UNDO_COMMANDS = frozenset({"PREVIOUS", "UNDO", "ANTERIOR"})


def _load_query_catalog(path: Path) -> list[str]:
    """Read expressions from a queries JSON file."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OSError(f"Could not read queries file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Queries file {path} is not valid JSON. Detail: {exc}"
        ) from exc
    if isinstance(data, dict) and isinstance(data.get("queries"), list):
        items = data["queries"]
    elif isinstance(data, dict) and isinstance(data.get("consultas"), list):
        # Pre-rename catalogs used the Spanish key.
        items = data["consultas"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError(
            f"Unexpected format in {path}; expected "
            '{"queries": ["expression", ...]}.'
        )
    expressions: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            expressions.append(item.strip())
        elif isinstance(item, dict) and (
            item.get("expression") or item.get("expresion")
        ):
            expressions.append(
                str(item.get("expression") or item.get("expresion")).strip()
            )
    return expressions


def _save_query_catalog(queries: list[str], path: Path) -> None:
    """Write expressions atomically as {"queries": [...]}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"queries": queries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _compact_age_band_token(label: str) -> str:
    """Turn '15 to 19 years' / '80 and over' into '15-19' / '80+'."""
    text = str(label).strip()
    if text.endswith(" and over"):
        return text.split()[0] + "+"
    cleaned = text.removesuffix(" years").strip()
    if " to " in cleaned:
        low, high = cleaned.split(" to ", 1)
        return f"{low.strip()}-{high.strip()}"
    return cleaned.replace(" ", "-")


def batch_segmentation_header_lines(
    config: SegmentationConfig | None = None,
) -> list[str]:
    """Comment lines describing each batch column group.

    Default (age + gender):
      # 15-19|20-24|...|80+
      # M-F|M-F|...|M-F
    Each M-F slot is two numbers in the data row (Men, Women).
    """
    cfg = config or get_segmentation()
    if cfg.age_segmentation and cfg.gender_segmentation:
        bands = [_compact_age_band_token(label) for label in cfg.age_range.labels()]
        return [
            "# " + "|".join(bands),
            "# " + "|".join("M-F" for _ in bands),
        ]
    if cfg.age_segmentation:
        bands = [_compact_age_band_token(label) for label in cfg.age_range.labels()]
        return ["# " + "|".join(bands)]
    if cfg.gender_segmentation:
        return ["# M-F"]
    return ["# Count"]


def _batch_file_preamble(source_name: str, stamp: str) -> str:
    """Leading # comments for a new batch_*.csv (file, session, segmentation)."""
    source = sanitize_csv_comment(source_name)
    lines = [
        f"# File: {source}",
        f"# Session: {stamp}",
        *batch_segmentation_header_lines(),
    ]
    return "\n".join(lines) + "\n"


def _table_to_batch_number_line(table: pd.DataFrame) -> str:
    """One line of counts: Men, Women, Men, Women, ... per age band.

    Grand-total columns are stopped at, not included. The batch file is a
    grid you can paste into a spreadsheet: one query per row.
    """
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
    return ", ".join(str(value) for value in values)


def _remove_last_batch_line(path: Path) -> bool:
    """Delete the last non-comment line of the batch file.

    That line may be a count row or a blank separator. Other blank lines
    and the # header comments are kept. The file is removed when nothing
    but comments would remain.
    """
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        path.unlink(missing_ok=True)
        return True
    lines = raw.splitlines()
    last_data: int | None = None
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].lstrip().startswith("#"):
            last_data = index
            break
    if last_data is None:
        return False
    del lines[last_data]
    still_has_data = any(not line.lstrip().startswith("#") for line in lines)
    if not lines or not still_has_data:
        path.unlink(missing_ok=True)
        return True
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig", newline="")
    return True


def _last_batch_line_is_blank(path: Path) -> bool:
    """True when the last non-comment batch line is an empty separator."""
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].lstrip().startswith("#"):
            continue
        return not lines[index].strip()
    return False


def _append_blank_batch_row(
    *,
    batch_path: Path,
    first_write: bool,
    stamp: str,
    source_name: str,
) -> bool:
    """Append a blank separator row. Returns False once the file exists."""
    if first_write:
        batch_path.write_text(
            _batch_file_preamble(source_name, stamp) + "\n",
            encoding="utf-8-sig",
            newline="",
        )
        return False
    with batch_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write("\n")
    return False


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _is_command(phrase: str, names: frozenset[str]) -> bool:
    return _collapse_spaces(phrase).upper() in names


def resolve_data_path(entry: str) -> Path:
    """Resolve a path, trying data/ when a relative name is not in the cwd.

    Absolute paths are never prefixed with data/. A missing path is returned
    unresolved so the caller can show both places it looked.
    """
    raw = entry.strip()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve() if path.is_file() else path
    if path.is_file():
        return path.resolve()
    under_data = (DATA_DIR / raw).expanduser()
    if under_data.is_file():
        return under_data.resolve()
    return path


def resolve_queries_path(entry: str) -> Path:
    """Resolve a queries JSON path, trying queries/ for relative names."""
    raw = entry.strip()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve() if path.is_file() else path
    if path.is_file():
        return path.resolve()
    under_queries = (QUERIES_DIR / raw).expanduser()
    if under_queries.is_file():
        return under_queries.resolve()
    # Allow omitting the .json suffix.
    if not raw.lower().endswith(".json"):
        with_suffix = (QUERIES_DIR / f"{raw}.json").expanduser()
        if with_suffix.is_file():
            return with_suffix.resolve()
        local_suffix = Path(f"{raw}.json")
        if local_suffix.is_file():
            return local_suffix.resolve()
    return path


def _suggested_files(*, kind: str = "data") -> list[str]:
    found: set[str] = set()
    if kind == "queries":
        bases = (QUERIES_DIR, Path("."))
        patterns = ("*.json",)
    else:
        bases = (DATA_DIR, Path("."))
        patterns = ("*.csv", "*.tsv", "*.xlsx", "*.xls")
    for base in bases:
        if not base.is_dir():
            continue
        for pattern in patterns:
            for path in base.glob(pattern):
                if path.is_file():
                    found.add(str(path))
    return sorted(found)


def _setup_tab_completion(*, kind: str = "data") -> None:
    """Tab-complete data spreadsheets or queries JSON files."""

    def complete(text: str, state: int) -> str | None:
        if state == 0:
            files = _suggested_files(kind=kind)
            if text:
                matches = [
                    path
                    for path in files
                    if Path(path).name.startswith(text)
                    or path.startswith(text)
                    or text.lower() in Path(path).name.lower()
                ]
            else:
                matches = files
            complete.matches = sorted(set(matches))  # type: ignore[attr-defined]
        matches: list[str] = getattr(complete, "matches", [])
        if state < len(matches):
            return matches[state]
        return None

    complete.matches = []  # type: ignore[attr-defined]
    readline.set_completer(complete)
    for binding in ("tab: complete", "bind ^I rl_complete"):
        try:
            readline.parse_and_bind(binding)
            break
        except Exception:
            continue


def _read_line(prompt: str) -> str:
    return input(prompt).strip()


def _run_full_query(
    *,
    df: pd.DataFrame,
    phrase: str,
    source: Path,
    query_number: int,
    output_dir: Path,
    batch_path: Path,
    first_batch_write: bool,
    stamp: str,
    age_column: str,
    sex_column: str,
) -> tuple[int, bool, Path | None]:
    """Parse, print, and save one query.

    Returns the new query number, whether the batch file is still empty,
    and the per-query CSV path (None in silent mode).
    """
    silent = get_app_config().silent_mode
    mask, descriptions, label = build_expression_mask(df, phrase, resolve_column)
    query_number += 1
    title = "Query (boolean expression over columns)"
    description = title + "\n" + "\n".join(f"  • {item}" for item in descriptions)

    table = run_query_on_mask(
        df,
        mask,
        condition_description=description,
        age_column=age_column,
        sex_column=sex_column,
        include_totals=True,
        short_label=label,
    )

    print(f"\n--- Age and sex table · {label} ---\n")
    print(table.to_string())

    output: Path | None = None
    if silent:
        print("\n(Silent mode: no per-query CSV; see config/app.conf SILENT_MODE.)")
    else:
        slug = slug_for_filename(label)
        csv_name = f"query_{source.stem}_{query_number:03d}_{slug}.csv"
        output = output_dir / csv_name
        safe_label = sanitize_csv_comment(label)
        safe_source = sanitize_csv_comment(source.name)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            handle.write(f"# Query: {safe_label}\n")
            handle.write(f"# File: {safe_source}\n")
        table.to_csv(output, mode="a", index=False)
        print(f"\nCSV saved: {output.resolve()}")

    number_line = _table_to_batch_number_line(table)
    if first_batch_write:
        batch_path.write_text(
            _batch_file_preamble(source.name, stamp) + number_line + "\n",
            encoding="utf-8-sig",
            newline="",
        )
        first_batch_write = False
    else:
        with batch_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(number_line + "\n")
    print(
        "Batch file (one line per query: M, W, M, W, … across age bands, no totals): "
        f"{batch_path.resolve()}"
    )
    print()
    return query_number, first_batch_write, output


def _print_expression_help() -> None:
    print(
        "Filter expression (AND / OR, parentheses; ';' and a new line mean AND).\n"
        "  Text:    COLUMN = VALUE    |    COLUMN *= text (contains)\n"
        "  Number:  COLUMN < 8%   COLUMN <= 60   COLUMN >= 30   COLUMN = 6.2\n"
        "  Logic:   AND   OR   ( … )     also: &&  ||  ;\n"
        "  Precedence: AND before OR (as in SQL). Use parentheses to group.\n\n"
        "Examples:\n"
        f"  {COL_CV_RISK} = BAJO\n"
        f"  {COL_DIABETES_MELLITUS_2} = SI; {COL_HBA1C_VALUE} < 8%\n"
        "  (VFG valor <= 60 OR VALOR RAC >= 30) AND LOSARTAN O ENALAPRIL (SI,NO) = SI\n"
        "  HINT CURRENT  → the FECHA column for HINT is within the last 12 months\n"
        "    e.g. RAC CURRENT, VFG VIGENTE, FONDO DE OJO CURRENT,\n"
        "    ULTIMA ATENCION CON PODOLOGA VIGENTE, ELECTRO CURRENT\n"
        "  DIABETES = SI   → PSCV type 2 diabetes diagnosis column\n"
        "  RCV = BAJO      → cardiovascular risk column (BAJO, MODERADO, ALTO)\n"
        "  RAC >= 30       → ACR value (not the same as RAC CURRENT, which is a date)\n"
        "  VFG <= 60       → eGFR value (not the same as VFG CURRENT, which is a date)\n"
        "  HBA1C < 8%      → HbA1c value (El Yeco and San José)\n"
        "  AMPUTATION = SI → diabetic-foot amputation column\n"
        "  INSULIN = SI    → 'PACIENTE CON INSULINA' "
        "(San José spells the header '(SI, NO)')\n"
        "  CKD / ERC / ERC = SI → (RAC >= 30 OR VFG < 60)\n"
        "  HYPERTENSION    → hypertension diagnosis = SI "
        "(either 'DIAGNOSTICO  PSCV DE HTA' or 'DIAGNOSTICO  HTA')\n\n"
        "Cell values stay as in the workbook: SI, NO, BAJO, MODERADO, ALTO.\n"
        "SILENT_MODE in config/app.conf defaults to true: only batch_*.csv under output/.\n"
        "Set SILENT_MODE=false to also write a query_*.csv per expression.\n"
        "Interactive sessions (no queries file) are saved at the end to\n"
        f"  {QUERIES_DIR}/queries_<timestamp>.json\n"
        "The batch_*.csv file stores only numbers, one line per query, in order "
        "Men, Women, Men, Women, … by age band (comma-separated), without Men/Women "
        "grand totals. It starts once with # File, # Session, and two segmentation "
        "comment rows (# 15-19|20-24|… and # M-F|M-F|…).\n"
        "To finish: press Enter on an empty 'Expression:' prompt.\n"
        "  BLANK / SPACE / ESPACIO → blank separator row in the batch file "
        "(for spacing/layout when pasting).\n"
        "  PREVIOUS  → remove the last batch line or blank row, and the last CSV\n"
        "              if one was written (ANTERIOR and UNDO do the same).\n"
    )


def _run_expression_list(
    *,
    expressions: list[str],
    frame: pd.DataFrame,
    source: Path,
    output_dir: Path,
    batch_path: Path,
    stamp: str,
    age_column: str,
    sex_column: str,
) -> tuple[int, bool, Path | None]:
    """Run a fixed list of expressions (from a queries file)."""
    first_batch_write = True
    query_number = 0
    last_csv: Path | None = None
    for index, phrase in enumerate(expressions, start=1):
        print("-" * 60)
        print(f"[{index}/{len(expressions)}] {phrase}")
        if _is_command(phrase, BLANK_COMMANDS):
            first_batch_write = _append_blank_batch_row(
                batch_path=batch_path,
                first_write=first_batch_write,
                stamp=stamp,
                source_name=source.name,
            )
            print("Blank row added to the batch file (separator).\n")
            continue
        try:
            query_number, first_batch_write, last_csv = _run_full_query(
                df=frame,
                phrase=phrase,
                source=source,
                query_number=query_number,
                output_dir=output_dir,
                batch_path=batch_path,
                first_batch_write=first_batch_write,
                stamp=stamp,
                age_column=age_column,
                sex_column=sex_column,
            )
        except Exception as exc:
            print(
                f"\nError in query {index}/{len(expressions)}:\n"
                f"  {phrase}\n"
                f"  {exc}\n"
                "Stopped so you can fix that expression.",
                file=sys.stderr,
            )
            sys.exit(1)
    return query_number, first_batch_write, last_csv


def main() -> None:
    ensure_app_workdir()
    tty = sys.stdin.isatty()
    if tty:
        _setup_tab_completion(kind="data")
        print(
            "1) Data file (relative names are also looked up in data/).\n"
            "   Tab completes files in ./ and data/.\n"
            "   Examples: yeco.csv    or    diabetes-yeco.csv\n"
            "\n"
            "   Tip (Windows package for someone without Python):\n"
            "   On a Windows PC run:  python build_windows_exe.py\n"
            "   Then zip the folder dist\\SheetQuery\\ and send it.\n"
            "   Or use the no-prompt runner:  python run_batch.py\n"
        )
    else:
        print("(Non-interactive input: Tab completion is off.)\n")

    raw_path = _read_line("Data file: ")
    if not raw_path:
        print("Cancelled.", file=sys.stderr)
        sys.exit(1)
    source = resolve_data_path(raw_path)
    if not source.is_file():
        tried = Path(raw_path.strip()).expanduser()
        under_data = (DATA_DIR / raw_path.strip()).expanduser()
        print(
            "Data file not found.\n"
            f"  Tried: {tried.resolve()}\n"
            f"  Tried: {under_data.resolve()}",
            file=sys.stderr,
        )
        sys.exit(1)

    if tty:
        _setup_tab_completion(kind="queries")
        print(
            "\n2) Queries file (optional). Empty Enter = type queries interactively.\n"
            "   Relative names are looked up in queries/. Tab completes *.json.\n"
            "   Examples: dani_y.json    or    dani_y\n"
        )
    raw_queries = _read_line("Queries file (optional): ")
    queries_path: Path | None = None
    catalog: list[str] = []
    if raw_queries.strip():
        queries_path = resolve_queries_path(raw_queries)
        if not queries_path.is_file():
            tried = Path(raw_queries.strip()).expanduser()
            under_queries = (QUERIES_DIR / raw_queries.strip()).expanduser()
            print(
                "Queries file not found.\n"
                f"  Tried: {tried.resolve()}\n"
                f"  Tried: {under_queries.resolve()}",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            catalog = _load_query_catalog(queries_path)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        if not catalog:
            print(f"Queries file is empty: {queries_path}", file=sys.stderr)
            sys.exit(1)

    frame = load_data(source, separator=None, sheet=None)
    frame = frame.dropna(how="all")
    try:
        age_column, sex_column = resolve_age_sex_columns(frame)
        dedup_column = resolve_dedup_column(frame)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"\nLoaded: {len(frame)} rows, {len(frame.columns)} columns.")
    print(f"Data: {source.resolve()}")
    if queries_path is not None:
        print(f"Queries: {queries_path.resolve()} ({len(catalog)} expressions)")
    else:
        print("Queries: (interactive — session will be saved to a new file at the end)")
    print(f"Age: {age_column} · Sex: {sex_column}")
    if dedup_column is not None:
        print(f"Dedup: {dedup_column} (one row per person before counting)")
    else:
        print("Dedup: off (DEDUP_COLUMN empty in config/segmentation.conf)")
    app_cfg = get_app_config()
    if app_cfg.silent_mode:
        print(
            "Silent mode ON (config/app.conf SILENT_MODE=true): "
            "only batch_*.csv will be written; no per-query CSV files.\n"
        )
    else:
        print(
            "Silent mode OFF (config/app.conf SILENT_MODE=false): "
            "each query also writes query_*.csv under output/.\n"
        )

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_path = output_dir / f"batch_{source.stem}_{stamp}.csv"
    first_batch_write = True
    query_number = 0
    last_csv: Path | None = None
    session_expressions: list[str] = []
    pd.set_option("display.width", 140)
    pd.set_option("display.max_rows", 30)

    if queries_path is not None:
        print(
            f"Running {len(catalog)} quer"
            f"{'y' if len(catalog) == 1 else 'ies'} from {queries_path.name}...\n"
        )
        query_number, first_batch_write, last_csv = _run_expression_list(
            expressions=catalog,
            frame=frame,
            source=source,
            output_dir=output_dir,
            batch_path=batch_path,
            stamp=stamp,
            age_column=age_column,
            sex_column=sex_column,
        )
        print(f"Done. Batch file: {batch_path.resolve()}")
        return

    _print_expression_help()
    print(f"Batch file for this run: {batch_path.name}")
    print(f"When you finish, expressions will be saved under {QUERIES_DIR}/\n")

    while True:
        print("-" * 60)
        first_line = _read_line("Expression: ")
        if not first_line.strip():
            break
        lines = [first_line]
        while True:
            extra = _read_line("  (empty Enter = run this query) ")
            if not extra:
                break
            lines.append(extra)
        phrase = "\n".join(lines)
        if not phrase.strip():
            break

        if _is_command(phrase, BLANK_COMMANDS):
            first_batch_write = _append_blank_batch_row(
                batch_path=batch_path,
                first_write=first_batch_write,
                stamp=stamp,
                source_name=source.name,
            )
            # Keep the word the user typed (BLANK, SPACE, or ESPACIO).
            session_expressions.append(_collapse_spaces(phrase).upper())
            print(f"Blank row added to the batch file: {batch_path.resolve()}\n")
            continue

        if _is_command(phrase, UNDO_COMMANDS):
            if _last_batch_line_is_blank(batch_path):
                if not _remove_last_batch_line(batch_path):
                    print("  No batch file, or no blank row left to remove.\n")
                    continue
                first_batch_write = not batch_path.is_file()
                if session_expressions and session_expressions[-1] in BLANK_COMMANDS:
                    session_expressions.pop()
                print("  Blank separator row removed from the batch file.\n")
                continue
            if query_number <= 0:
                print("  No earlier query to undo in this session.\n")
                continue
            if not _remove_last_batch_line(batch_path):
                print("  No batch file, or no count line left to remove.\n")
                continue
            first_batch_write = not batch_path.is_file()
            query_number -= 1
            if last_csv is not None and last_csv.is_file():
                try:
                    last_csv.unlink()
                    print(f"  Deleted: {last_csv.name}")
                except OSError as exc:
                    print(f"  Could not delete {last_csv}: {exc}", file=sys.stderr)
            last_csv = None
            if session_expressions:
                session_expressions.pop()
            print("  Last batch line removed.\n")
            continue

        parsed = False
        while not parsed:
            try:
                query_number, first_batch_write, last_csv = _run_full_query(
                    df=frame,
                    phrase=phrase,
                    source=source,
                    query_number=query_number,
                    output_dir=output_dir,
                    batch_path=batch_path,
                    first_batch_write=first_batch_write,
                    stamp=stamp,
                    age_column=age_column,
                    sex_column=sex_column,
                )
                session_expressions.append(phrase.strip())
                parsed = True
            except ValueError as exc:
                print(f"  {exc}")
                phrase = _read_line(
                    "Try the expression again (empty Enter = skip this query): "
                ).strip()
                if not phrase:
                    print("  Query skipped.\n")
                    break
        if not parsed:
            continue

    print("\nDone.")
    if query_number > 0 or batch_path.is_file():
        print(f"Batch file: {batch_path.resolve()}")
    if session_expressions:
        session_path = QUERIES_DIR / f"queries_{stamp}.json"
        try:
            _save_query_catalog(session_expressions, session_path)
            print(
                f"Saved {len(session_expressions)} expression(s) to {session_path.resolve()}"
            )
        except OSError as exc:
            print(f"Could not save session queries: {exc}", file=sys.stderr)
    else:
        print("No expressions to save.")


if __name__ == "__main__":
    main()
