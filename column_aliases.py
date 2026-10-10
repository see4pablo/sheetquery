"""Load short column names from config/column_aliases.conf and resolve them.

Config format (see config/column_aliases.conf):
  SHORT_NAME=HEADER
  SHORT_NAME="HEADER WITH, COMMAS", "OTHER HEADER"

When several headers are listed, the first one that matches a column in the
workbook is used (compared by fingerprint: case, accents, and spaces ignored).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from io import StringIO
from pathlib import Path

import pandas as pd

DEFAULT_ALIASES_PATH = Path("config/column_aliases.conf")
BACKUP_ALIASES_PATH = Path("config/column_aliases.conf.backup")


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _alias_key(text: str) -> str:
    """Normalize an alias key: strip accents, collapse spaces, uppercase."""
    folded = unicodedata.normalize("NFKD", text.strip())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", folded).upper()


def column_fingerprint(text: str) -> str:
    """Compare headers ignoring spaces and accents."""
    folded = unicodedata.normalize("NFKD", text.strip())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"\s+", "", folded).upper()


def parse_aliases_text(text: str) -> dict[str, list[str]]:
    """Parse conf text into {ALIAS_KEY: [header, ...]}."""
    aliases: dict[str, list[str]] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"Line {line_number}: expected ALIAS=HEADER[, HEADER...], got {raw_line!r}"
            )
        key_raw, _, value_raw = line.partition("=")
        key = _alias_key(key_raw)
        if not key:
            raise ValueError(f"Line {line_number}: empty alias name.")
        # CSV so commas inside quoted headers stay intact.
        # skipinitialspace allows: "header one", "header two"
        try:
            headers = next(
                csv.reader(StringIO(value_raw.strip()), skipinitialspace=True)
            )
        except csv.Error as exc:
            raise ValueError(f"Line {line_number}: invalid header list: {exc}") from exc
        # Keep internal spacing from the spreadsheet (e.g. double space in
        # "DIAGNOSTICO  HTA"); only trim the ends of each candidate.
        cleaned = [header.strip() for header in headers if header.strip()]
        if not cleaned:
            raise ValueError(f"Line {line_number}: alias {key!r} has no headers.")
        aliases[key] = cleaned
    return aliases


def load_aliases(path: Path | None = None) -> dict[str, list[str]]:
    """Load aliases from `path`, or the default conf (then the backup)."""
    candidates = []
    if path is not None:
        candidates.append(path)
    else:
        candidates.extend([DEFAULT_ALIASES_PATH, BACKUP_ALIASES_PATH])

    for candidate in candidates:
        if candidate.is_file():
            return parse_aliases_text(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        "No column alias config found. Expected "
        f"{DEFAULT_ALIASES_PATH} or {BACKUP_ALIASES_PATH}."
    )


@lru_cache(maxsize=4)
def _cached_aliases(path_str: str | None) -> dict[str, tuple[str, ...]]:
    path = Path(path_str) if path_str else None
    loaded = load_aliases(path)
    return {key: tuple(headers) for key, headers in loaded.items()}


def get_aliases(path: Path | None = None) -> dict[str, list[str]]:
    """Return a fresh dict of aliases (cached by path under the hood)."""
    cached = _cached_aliases(str(path) if path is not None else None)
    return {key: list(headers) for key, headers in cached.items()}


def clear_alias_cache() -> None:
    """Drop the loaded config cache (for tests or after editing the file)."""
    _cached_aliases.cache_clear()


def pick_column_from_candidates(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the workbook column matching the first listed candidate present."""
    by_fingerprint = {
        column_fingerprint(candidate): candidate for candidate in candidates
    }
    wanted = set(by_fingerprint)
    for column in df.columns:
        if column_fingerprint(str(column)) in wanted:
            return str(column)
    return None


def resolve_column(
    df: pd.DataFrame,
    name_or_fragment: str,
    *,
    aliases_path: Path | None = None,
) -> str:
    """Resolve an alias, an exact header, or a unique substring to one column.

    Short names come from config/column_aliases.conf. If several headers are
    configured for one alias, the first match in the workbook wins. If several
    headers contain the typed text as a substring, the user is asked to pick.
    """
    name = _collapse_spaces(name_or_fragment)
    key = _alias_key(name)
    aliases = get_aliases(aliases_path)

    if key in aliases:
        picked = pick_column_from_candidates(df, aliases[key])
        if picked is not None:
            return picked
        expected = ", ".join(repr(header) for header in aliases[key])
        raise ValueError(
            f"No column found for alias {key!r}. Expected one of: {expected}"
        )

    # Typed text may be a full configured header (any alias's candidate).
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

    print("\nSeveral columns match; pick a number:\n")
    for index, column in enumerate(candidates, start=1):
        print(f"  {index}. {column}")
    raw = input("\nNumber: ").strip()
    index = int(raw)
    if 1 <= index <= len(candidates):
        return str(candidates[index - 1])
    raise ValueError("Invalid selection.")
