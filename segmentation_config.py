"""Load report segmentation settings from config/segmentation.conf.

Controls whether results are split by age bands and/or sex, which spreadsheet
headers hold those values, and how age bands are cut.

See config/segmentation.conf for the format. Defaults match the historical
CESFAM tables: age on from 15 in steps of 5 with an open 80+ band, and sex on.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from io import StringIO
from pathlib import Path

DEFAULT_SEGMENTATION_PATH = Path("config/segmentation.conf")
BACKUP_SEGMENTATION_PATH = Path("config/segmentation.conf.backup")

_TRUE = frozenset({"TRUE", "YES", "Y", "1", "ON", "SI", "SÍ"})
_FALSE = frozenset({"FALSE", "NO", "N", "0", "OFF"})
# [15:5:80+] → start=15, step=5, open-ended band begins at 80.
_AGE_RANGE_RE = re.compile(r"^\[?\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*\+?\s*\]?$")


@dataclass(frozen=True)
class AgeRange:
    """Half-open bands [start, start+step), …, then [open_from, ∞)."""

    start: int
    step: int
    open_from: int

    def edges(self) -> list[float]:
        if self.step <= 0:
            raise ValueError(f"AGE_RANGE step must be positive, got {self.step}")
        if self.open_from < self.start:
            raise ValueError(
                f"AGE_RANGE open end ({self.open_from}) is before start ({self.start})"
            )
        if (self.open_from - self.start) % self.step != 0:
            raise ValueError(
                f"AGE_RANGE open end {self.open_from} is not reachable from "
                f"{self.start} with step {self.step}"
            )
        return list(range(self.start, self.open_from + 1, self.step)) + [float("inf")]

    def labels(self) -> list[str]:
        edges = self.edges()
        labels: list[str] = []
        for index in range(len(edges) - 2):
            low = int(edges[index])
            high = int(edges[index + 1]) - 1
            labels.append(f"{low} to {high} years")
        labels.append(f"{self.open_from} and over")
        return labels


@dataclass(frozen=True)
class SegmentationConfig:
    age_segmentation: bool
    age_columns: tuple[str, ...]
    age_range: AgeRange
    gender_segmentation: bool
    gender_columns: tuple[str, ...]


def _parse_bool(raw: str, *, key: str, line_number: int) -> bool:
    token = raw.strip().upper()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise ValueError(f"Line {line_number}: {key} must be true/false, got {raw!r}")


def _parse_header_list(raw: str, *, key: str, line_number: int) -> tuple[str, ...]:
    try:
        headers = next(csv.reader(StringIO(raw.strip()), skipinitialspace=True))
    except csv.Error as exc:
        raise ValueError(f"Line {line_number}: invalid {key} list: {exc}") from exc
    cleaned = tuple(header.strip() for header in headers if header.strip())
    if not cleaned:
        raise ValueError(f"Line {line_number}: {key} has no headers.")
    return cleaned


def parse_age_range(raw: str) -> AgeRange:
    """Parse `[15:5:80+]` or `15:5:80+` into an AgeRange."""
    match = _AGE_RANGE_RE.fullmatch(raw.strip())
    if not match:
        raise ValueError(
            f"Invalid AGE_RANGE {raw!r}. Use [start:step:open+] "
            f"(example: [15:5:80+])."
        )
    start, step, open_from = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )
    return AgeRange(start=start, step=step, open_from=open_from)


def parse_segmentation_text(text: str) -> SegmentationConfig:
    """Parse a segmentation.conf body into a SegmentationConfig."""
    values: dict[str, tuple[int, str]] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"Line {line_number}: expected KEY=VALUE, got {raw_line!r}"
            )
        key_raw, _, value_raw = line.partition("=")
        key = key_raw.strip().upper()
        # SEX_COLUMN is accepted as a synonym of GENDER_COLUMN.
        if key == "SEX_COLUMN":
            key = "GENDER_COLUMN"
        values[key] = (line_number, value_raw.strip())

    def require(key: str) -> tuple[int, str]:
        if key not in values:
            raise ValueError(f"Missing required key {key} in segmentation config.")
        return values[key]

    age_line, age_raw = require("AGE_SEGMENTATION")
    gender_line, gender_raw = require("GENDER_SEGMENTATION")
    age_on = _parse_bool(age_raw, key="AGE_SEGMENTATION", line_number=age_line)
    gender_on = _parse_bool(
        gender_raw, key="GENDER_SEGMENTATION", line_number=gender_line
    )

    age_col_line, age_col_raw = require("AGE_COLUMN")
    gender_col_line, gender_col_raw = require("GENDER_COLUMN")
    age_columns = _parse_header_list(
        age_col_raw, key="AGE_COLUMN", line_number=age_col_line
    )
    gender_columns = _parse_header_list(
        gender_col_raw, key="GENDER_COLUMN", line_number=gender_col_line
    )

    range_line, range_raw = require("AGE_RANGE")
    try:
        age_range = parse_age_range(range_raw)
    except ValueError as exc:
        raise ValueError(f"Line {range_line}: {exc}") from exc

    return SegmentationConfig(
        age_segmentation=age_on,
        age_columns=age_columns,
        age_range=age_range,
        gender_segmentation=gender_on,
        gender_columns=gender_columns,
    )


def load_segmentation(path: Path | None = None) -> SegmentationConfig:
    """Load segmentation settings from `path`, or the default conf (then backup)."""
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        candidates.extend([DEFAULT_SEGMENTATION_PATH, BACKUP_SEGMENTATION_PATH])

    for candidate in candidates:
        if candidate.is_file():
            return parse_segmentation_text(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        "No segmentation config found. Expected "
        f"{DEFAULT_SEGMENTATION_PATH} or {BACKUP_SEGMENTATION_PATH}."
    )


@lru_cache(maxsize=4)
def _cached_segmentation(path_str: str | None) -> SegmentationConfig:
    path = Path(path_str) if path_str else None
    return load_segmentation(path)


def get_segmentation(path: Path | None = None) -> SegmentationConfig:
    return _cached_segmentation(str(path) if path is not None else None)


def clear_segmentation_cache() -> None:
    _cached_segmentation.cache_clear()
