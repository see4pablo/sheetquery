"""Load general app settings from config/app.conf.

See config/app.conf for keys and defaults. A snapshot lives in app.conf.backup.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_APP_CONFIG_PATH = Path("config/app.conf")
BACKUP_APP_CONFIG_PATH = Path("config/app.conf.backup")

_TRUE = frozenset({"TRUE", "YES", "Y", "1", "ON", "SI", "SÍ"})
_FALSE = frozenset({"FALSE", "NO", "N", "0", "OFF"})


@dataclass(frozen=True)
class AppConfig:
    """Runtime flags that are not about columns or segmentation."""

    silent_mode: bool
    """If True (default), skip per-query CSV files; only write the batch file."""


def _parse_bool(raw: str, *, key: str, line_number: int) -> bool:
    token = raw.strip().upper()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise ValueError(f"Line {line_number}: {key} must be true/false, got {raw!r}")


def parse_app_config_text(text: str) -> AppConfig:
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
        values[key] = (line_number, value_raw.strip())

    if "SILENT_MODE" not in values:
        raise ValueError("Missing required key SILENT_MODE in app config.")
    line_number, raw = values["SILENT_MODE"]
    silent = _parse_bool(raw, key="SILENT_MODE", line_number=line_number)
    return AppConfig(silent_mode=silent)


def load_app_config(path: Path | None = None) -> AppConfig:
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        candidates.extend([DEFAULT_APP_CONFIG_PATH, BACKUP_APP_CONFIG_PATH])
    for candidate in candidates:
        if candidate.is_file():
            return parse_app_config_text(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        "No app config found. Expected "
        f"{DEFAULT_APP_CONFIG_PATH} or {BACKUP_APP_CONFIG_PATH}."
    )


@lru_cache(maxsize=4)
def _cached_app_config(path_str: str | None) -> AppConfig:
    path = Path(path_str) if path_str else None
    return load_app_config(path)


def get_app_config(path: Path | None = None) -> AppConfig:
    return _cached_app_config(str(path) if path is not None else None)


def clear_app_config_cache() -> None:
    _cached_app_config.cache_clear()
