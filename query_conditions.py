"""Parse and evaluate filter expressions against a PSCV spreadsheet.

Atomic conditions:
  COLUMN = text          normalized equality
  COLUMN *= text         substring (also ~=)
  COLUMN < 8             numeric; also <=, >, >=, and = number
  HINT CURRENT           the date column for HINT is within the last 12 months
                         (Spanish synonym: VIGENTE)

Boolean expressions use AND / OR, parentheses, and the synonyms ";", "&&",
and "||". AND binds tighter than OR, as in SQL.

Two shorthand words expand before parsing:
  CKD or ERC, and "ERC = SI", become (ACR >= 30 OR eGFR < 60).
  HYPERTENSION or HIPERTENSION becomes the hypertension diagnosis column = SI.
  "HYPERTENSION = ..." is left alone so it is not expanded twice.
  "ERC = NO" and any other "ERC = <value>" still hit the ERC column itself.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, TypedDict

import numpy as np
import pandas as pd

from age_sex_segmentation import filter_column_value
from pscv_columns import COL_ACR_DATE


class TextCondition(TypedDict):
    kind: Literal["text"]
    col_raw: str
    value: str
    mode: Literal["normalized_equals", "contains"]


class NumberCondition(TypedDict):
    kind: Literal["number"]
    col_raw: str
    op: Literal["<", "<=", ">", ">=", "=="]
    threshold: float


class CurrentDateCondition(TypedDict):
    """HINT CURRENT: the date stored for HINT falls inside the last N months."""

    kind: Literal["current_date"]
    hint_raw: str


Condition = TextCondition | NumberCondition | CurrentDateCondition

# Words that mark "this control's date is still within 12 months".
_CURRENT_WORD = r"(?:VIGENTE|CURRENT)"


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def parse_float_threshold(text: str) -> float:
    """Parse 8, 8%, 8,5, 6.2, and the same forms with surrounding spaces."""
    cleaned = text.strip().replace("%", "").replace(",", ".").strip()
    if not cleaned:
        raise ValueError("empty numeric value")
    return float(cleaned)


def cell_to_float(value: Any) -> float:
    """Convert one cell to float. Non-numeric and 'does not apply' cells become NaN."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    if not text:
        return np.nan
    if text.upper() in ("NO APLICA", "NAN", "N/A", "NA", "-", "—", "DESCONOCIDO"):
        return np.nan
    text = text.replace("%", "").replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        return np.nan


def _try_numeric_condition(text: str) -> NumberCondition | None:
    """Parse a numeric comparison, or return None when the right-hand side is text.

    Operators are tried longest-first so "<=" is not read as "<".
    A bare "=" is numeric only when the right-hand side parses as a number;
    otherwise the caller treats it as a text match.
    """
    for op in ("<=", ">=", "<", ">"):
        if op not in text:
            continue
        left, right = text.rsplit(op, 1)
        left, right = left.strip(), right.strip()
        try:
            threshold = parse_float_threshold(right)
        except ValueError:
            continue
        if not left:
            continue
        return NumberCondition(
            kind="number",
            col_raw=_collapse_spaces(left),
            op=op,  # type: ignore[typeddict-item]
            threshold=threshold,
        )
    match = re.match(r"^(.+?)\s*=\s*(.+)$", text)
    if match:
        left, right = match.group(1).strip(), match.group(2).strip()
        try:
            threshold = parse_float_threshold(right)
        except ValueError:
            return None
        return NumberCondition(
            kind="number",
            col_raw=_collapse_spaces(left),
            op="==",
            threshold=threshold,
        )
    return None


def _resolve_acr_date_column(df: pd.DataFrame) -> str:
    if COL_ACR_DATE in df.columns:
        return COL_ACR_DATE
    for column in df.columns:
        folded = str(column).upper().replace("  ", " ")
        if "FECHA" in folded and "RAC" in folded:
            return str(column)
    raise ValueError("No ACR date column found (expected a header like 'FECHA  RAC').")


def _resolve_lipid_profile_date_column(df: pd.DataFrame) -> str:
    """El Yeco uses 'FECHA PERFIL LIPIDICO'; San José uses 'FECHA P LIPIDICO'."""
    columns = list(df.columns)
    for expected in ("FECHA PERFIL LIPIDICO", "FECHA P LIPIDICO"):
        target = _collapse_spaces(expected).upper()
        for column in columns:
            if _collapse_spaces(str(column)).upper() == target:
                return str(column)
    candidates = [
        str(column)
        for column in columns
        if "FECHA" in str(column).upper() and "LIPIDICO" in str(column).upper()
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(
        "No lipid-profile date column found "
        "(expected 'FECHA PERFIL LIPIDICO' or 'FECHA P LIPIDICO')."
    )


def _normalize_column_name(column: Any) -> str:
    return _collapse_spaces(str(column)).upper()


def resolve_date_column_by_hint(
    df: pd.DataFrame,
    hint: str,
    resolve_column: Callable[[pd.DataFrame, str], str],
) -> str:
    """Find the date column that belongs to a 'HINT CURRENT' condition.

    Examples: VFG -> FECHA VFG; RAC -> FECHA  RAC; FONDO DE OJO -> FECHA FONDO DE OJO.
    ACR and lipid profile have their own resolvers because those headers vary.
    Anything else is looked up as 'FECHA <hint>', then by token overlap, then
    delegated to `resolve_column`.
    """
    cleaned = _collapse_spaces(hint)
    if not cleaned:
        raise ValueError(
            "Say which control the currency refers to "
            "(e.g. VFG CURRENT, RAC CURRENT)."
        )
    key = cleaned.upper()
    if key == "RAC":
        return _resolve_acr_date_column(df)
    if key in ("PERFIL LIPIDICO", "P LIPIDICO"):
        return _resolve_lipid_profile_date_column(df)

    columns = list(df.columns)
    for prefix in ("FECHA ", "FECHA  "):
        candidate = _normalize_column_name(prefix + cleaned)
        for column in columns:
            if _normalize_column_name(column) == candidate:
                return str(column)

    for column in columns:
        if _normalize_column_name(column) == key:
            return str(column)

    date_columns = [
        column for column in columns if "FECHA" in _normalize_column_name(column)
    ]
    tokens = [token for token in re.split(r"\s+", cleaned.upper()) if len(token) > 1]
    matches: list[str] = []
    for column in date_columns:
        normalized = _normalize_column_name(column)
        if tokens and all(token in normalized for token in tokens):
            matches.append(str(column))
    if len(matches) == 1:
        return matches[0]
    return resolve_column(df, f"FECHA {cleaned}")


def current_date_mask(
    df: pd.DataFrame,
    column: str,
    *,
    reference: datetime | None = None,
    months: int = 12,
) -> pd.Series:
    """True when `column` holds a parseable date from (today - months) through today.

    Dates are read day-first (DD/MM/YYYY), which is how the workbooks store them.
    Unparseable cells are false, not errors.
    """
    if column not in df.columns:
        raise ValueError(f"Column {column!r} does not exist.")
    today = pd.Timestamp(reference or datetime.now()).normalize()
    start = today - pd.DateOffset(months=months)
    parsed = pd.to_datetime(
        df[column].astype(str).str.strip(),
        dayfirst=True,
        errors="coerce",
    )
    normalized = parsed.dt.normalize()
    mask = (normalized >= start) & (normalized <= today) & parsed.notna()
    return mask.fillna(False)


def parse_atomic_condition(line: str) -> Condition:
    """Parse one condition with no AND/OR. Raises ValueError on a bad line."""
    text = line.strip()
    if not text:
        raise ValueError("Empty condition.")

    current = re.fullmatch(rf"(.+?)\s+{_CURRENT_WORD}", text, flags=re.IGNORECASE)
    if current:
        return CurrentDateCondition(
            kind="current_date",
            hint_raw=_collapse_spaces(current.group(1)),
        )

    contains = re.match(r"^(.+?)\s*\*=\s*(.+)$", text) or re.match(
        r"^(.+?)\s*~=\s*(.+)$", text
    )
    if contains:
        return TextCondition(
            kind="text",
            col_raw=_collapse_spaces(contains.group(1)),
            value=_collapse_spaces(contains.group(2)),
            mode="contains",
        )

    numeric = _try_numeric_condition(text)
    if numeric is not None:
        return numeric

    equals = re.match(r"^(.+?)\s*=\s*(.+)$", text)
    if equals:
        return TextCondition(
            kind="text",
            col_raw=_collapse_spaces(equals.group(1)),
            value=_collapse_spaces(equals.group(2)),
            mode="normalized_equals",
        )

    raise ValueError(
        "Unrecognized condition. Use: COLUMN = text | COLUMN *= text | "
        "COLUMN < 8 | COLUMN <= 8% | COLUMN > 7 | COLUMN = 6.2 | "
        "HINT CURRENT (date within the last 12 months; "
        "e.g. VFG CURRENT, RAC VIGENTE)"
    )


def split_conditions(phrase: str) -> list[str]:
    """Split a phrase on ';' or newlines. Blank pieces are dropped."""
    unified = phrase.replace("\r\n", "\n").replace("\r", "\n")
    pieces: list[str] = []
    for block in unified.split("\n"):
        for part in block.split(";"):
            part = part.strip()
            if part:
                pieces.append(part)
    return pieces


def numeric_comparison_mask(
    df: pd.DataFrame,
    column: str,
    op: str,
    threshold: float,
) -> pd.Series:
    """Compare numeric cells. Non-numeric cells are false, never an error.

    Equality uses a tiny absolute tolerance so '8' and '8.0' match.
    """
    values = df[column].map(cell_to_float)
    present = values.notna()
    if op == "<":
        compared = values < threshold
    elif op == "<=":
        compared = values <= threshold
    elif op == ">":
        compared = values > threshold
    elif op == ">=":
        compared = values >= threshold
    elif op == "==":
        raw = np.asarray(values, dtype=float)
        compared = pd.Series(
            np.isclose(raw, float(threshold), rtol=0.0, atol=1e-9, equal_nan=False),
            index=df.index,
        )
    else:
        raise ValueError(f"Unsupported numeric operator: {op}")
    return (compared & present).fillna(False)


def _technical_atom_description(
    df: pd.DataFrame, condition: Condition, column: str
) -> tuple[pd.Series, str]:
    """Evaluate one atom and return a stable description for logs and labels."""
    if condition["kind"] == "current_date":
        mask = current_date_mask(df, column)
        return mask, f"{column} current (last 12 months through today)"
    if condition["kind"] == "text":
        mask = filter_column_value(
            df, column, condition["value"], mode=condition["mode"]
        )
        description = f"{column} ({condition['mode']}) → {condition['value']!r}"
    else:
        mask = numeric_comparison_mask(
            df, column, condition["op"], condition["threshold"]
        )
        if condition["op"] == "==":
            description = f"{column} = {condition['threshold']}"
        else:
            description = f"{column} {condition['op']} {condition['threshold']}"
    return mask, description


def build_and_mask(
    df: pd.DataFrame,
    parts: list[str],
    resolve_column: Callable[[pd.DataFrame, str], str],
) -> tuple[pd.Series, list[str]]:
    """AND of atomic conditions. Prefer `build_expression_mask` for OR and parentheses."""
    if not parts:
        raise ValueError("Provide at least one condition.")
    masks: list[pd.Series] = []
    descriptions: list[str] = []
    for part in parts:
        condition = parse_atomic_condition(part)
        if condition["kind"] == "current_date":
            column = resolve_date_column_by_hint(
                df, condition["hint_raw"], resolve_column
            )
        else:
            column = resolve_column(df, condition["col_raw"])
        mask, description = _technical_atom_description(df, condition, column)
        masks.append(mask)
        descriptions.append(description)
    combined = pd.Series(True, index=df.index)
    for mask in masks:
        combined &= mask
    return combined, descriptions


def normalize_boolean_expression(phrase: str) -> str:
    """Turn ';', '&&', '||', and newlines into AND / OR, then collapse spaces."""
    text = phrase.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(";", " AND ")
    text = re.sub(r"\|\|", " OR ", text, flags=re.IGNORECASE)
    text = re.sub(r"&&", " AND ", text, flags=re.IGNORECASE)
    return _collapse_spaces(text)


def expand_atomic_aliases(text: str) -> str:
    """Replace reserved words with the condition they stand for.

    Bare HYPERTENSION / HIPERTENSION becomes (HYPERTENSION = SI). The short
    name is resolved later via config/column_aliases.conf, so San José and
    El Yeco can keep different hypertension headers.
    """
    expanded = re.sub(
        r"\b(?:HYPERTENSION|HIPERTENSION)\b(?!\s*=)",
        "(HYPERTENSION = SI)",
        text,
        flags=re.IGNORECASE,
    )
    chronic_kidney = "(RAC >= 30 OR VFG < 60)"
    expanded = re.sub(
        r"\b(?:CKD|ERC)\s*=\s*SI\b",
        chronic_kidney,
        expanded,
        flags=re.IGNORECASE,
    )
    expanded = re.sub(
        r"\b(?:CKD|ERC)\b(?!\s*=)",
        chronic_kidney,
        expanded,
        flags=re.IGNORECASE,
    )
    return expanded.strip()


def _strip_full_outer_parens(text: str) -> str | None:
    """Return the inside of a string wrapped in one matching pair of parentheses.

    Returns None when the outer parentheses do not cover the whole string,
    so "(a) AND (b)" is left intact.
    """
    text = text.strip()
    if not text.startswith("("):
        return None
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                if index == len(text) - 1:
                    return text[1:-1].strip()
                return None
    return None


def _strip_parens_loop(text: str) -> str:
    stripped = text.strip()
    while True:
        inner = _strip_full_outer_parens(stripped)
        if inner is None:
            return stripped
        stripped = inner


def split_top_level(text: str, op: str) -> list[str]:
    """Split on AND or OR only at parenthesis depth 0.

    The operator must be a whole word, so a column named "... AND ..." is
    not split, and "AND" inside a value is not treated as syntax when it
    sits against another letter.
    """
    depth = 0
    index = 0
    start = 0
    length = len(text)
    op_length = len(op)
    parts: list[str] = []
    while index < length:
        char = text[index]
        if char == "(":
            depth += 1
            index += 1
        elif char == ")":
            depth -= 1
            index += 1
        elif (
            depth == 0
            and index + op_length <= length
            and text[index : index + op_length].upper() == op.upper()
        ):
            before_ok = (
                index == 0 or text[index - 1].isspace() or text[index - 1] in "()"
            )
            after_ok = (
                index + op_length >= length
                or text[index + op_length].isspace()
                or text[index + op_length] in "()"
            )
            if before_ok and after_ok:
                parts.append(text[start:index].strip())
                start = index + op_length
                index = start
                continue
            index += 1
        else:
            index += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def build_expression_mask(
    df: pd.DataFrame,
    phrase: str,
    resolve_column: Callable[[pd.DataFrame, str], str],
) -> tuple[pd.Series, list[str], str]:
    """Evaluate a boolean expression.

    Returns the row mask, one technical description per atomic condition,
    and a short label suitable for a report title.

    Example:
      (VFG valor <= 60 OR VALOR RAC >= 30) AND LOSARTAN ... = SI
    """
    expression = expand_atomic_aliases(normalize_boolean_expression(phrase))
    if not expression:
        raise ValueError("Empty expression.")

    technical_atoms: list[str] = []

    def parse_atom(fragment: str) -> tuple[pd.Series, str]:
        fragment = fragment.strip()
        if not fragment:
            raise ValueError("Empty subexpression.")
        condition = parse_atomic_condition(fragment)
        if condition["kind"] == "current_date":
            column = resolve_date_column_by_hint(
                df, condition["hint_raw"], resolve_column
            )
        else:
            column = resolve_column(df, condition["col_raw"])
        mask, technical = _technical_atom_description(df, condition, column)
        technical_atoms.append(technical)
        return mask, _short_atom_label(condition, column)

    def parse_primary(fragment: str) -> tuple[pd.Series, str]:
        fragment = _strip_parens_loop(fragment)
        if len(split_top_level(fragment, "OR")) > 1:
            return parse_or(fragment)
        if len(split_top_level(fragment, "AND")) > 1:
            return parse_and(fragment)
        return parse_atom(fragment)

    def parse_and(fragment: str) -> tuple[pd.Series, str]:
        parts = split_top_level(fragment, "AND")
        if len(parts) == 1:
            return parse_primary(parts[0])
        pieces = [parse_primary(part) for part in parts]
        mask = pieces[0][0]
        for next_mask, _label in pieces[1:]:
            mask &= next_mask
        labels = [f"({label})" if " OR " in label else label for _mask, label in pieces]
        return mask, " AND ".join(labels)

    def parse_or(fragment: str) -> tuple[pd.Series, str]:
        parts = split_top_level(fragment, "OR")
        if len(parts) == 1:
            return parse_and(parts[0])
        pieces = [parse_and(part) for part in parts]
        mask = pieces[0][0]
        for next_mask, _label in pieces[1:]:
            mask |= next_mask
        labels = [
            f"({label})" if " AND " in label else label for _mask, label in pieces
        ]
        return mask, " OR ".join(labels)

    mask, label = parse_or(expression)
    return mask, technical_atoms, label


def short_column_name(column: str) -> str:
    """Short English name for titles. The spreadsheet header stays unchanged."""
    text = str(column).strip()
    upper = text.upper()
    date_prefix = re.match(r"^FECHA\s+", text, flags=re.IGNORECASE)
    if date_prefix:
        rest = text[date_prefix.end() :].strip()
        if rest and len(rest) <= 28:
            return rest
        if rest:
            return rest[:25] + "…"
    if "RIESGO" in upper and "CARDIOVASCULAR" in upper:
        return "CV risk"
    if "DIABETES" in upper and "MELLITUS" in upper and "2" in upper:
        return "T2DM (PSCV)"
    if "DISLIPIDEMIA" in upper:
        return "Dyslipidemia (PSCV)"
    if "DIAGNOSTICO" in upper and "HTA" in upper:
        return "Hypertension"
    if "HBA1C" in upper.replace(" ", "") or text.startswith("HbA1C"):
        return "HbA1c"
    if "GLICEMIA" in upper and "AYUNAS" in upper:
        return "Impaired fasting glucose"
    if "ECV" in upper and "CARDIOVASCULAR" in upper:
        return "Other CVD"
    if "VFG" in upper:
        return "eGFR"
    if "VALOR RAC" in upper or upper.strip() == "VALOR RAC":
        return "ACR"
    if "LOSARTAN" in upper and "ENALAPRIL" in upper:
        return "Losartan/Enalapril"
    if len(text) <= 36:
        return text
    return text[:33] + "…"


def _format_threshold_label(threshold: float) -> str:
    if threshold == int(threshold):
        return str(int(threshold))
    return f"{threshold:.4f}".rstrip("0").rstrip(".")


def _short_atom_label(condition: Condition, column: str) -> str:
    if condition["kind"] == "current_date":
        return f"{short_column_name(column)} current (12 months)"
    name = short_column_name(column)
    if condition["kind"] == "text":
        if condition["mode"] == "contains":
            return f'{name} contains "{condition["value"]}"'
        return f"{name} = {condition['value']}"
    rendered = _format_threshold_label(float(condition["threshold"]))
    if condition["op"] == "==":
        return f"{name} = {rendered}"
    return f"{name} {condition['op']} {rendered}"


def readable_label_from_descriptions(descriptions: list[str]) -> str:
    """Turn technical atom descriptions into a short joined label.

    Example: "CV risk = BAJO · HbA1c < 8.0"
    """
    parts: list[str] = []
    for description in descriptions:
        if "→" in description:
            left, right = description.split("→", 1)
            column = left.split("(")[0].strip()
            value = right.strip()
            if (value.startswith("'") and value.endswith("'")) or (
                value.startswith('"') and value.endswith('"')
            ):
                value = value[1:-1]
            if "(contains)" in left:
                parts.append(f'{short_column_name(column)} contains "{value}"')
            else:
                parts.append(f"{short_column_name(column)} = {value}")
            continue
        equality = re.match(r"^(.+) = ([0-9]+(?:[.,][0-9]+)?)\s*$", description.strip())
        if equality:
            parts.append(
                f"{short_column_name(equality.group(1).strip())} = "
                f"{equality.group(2).replace(',', '.')}"
            )
            continue
        for op in ("<=", ">=", "<", ">"):
            needle = f" {op} "
            if needle in description:
                column, _, rest = description.partition(needle)
                parts.append(f"{short_column_name(column.strip())} {op} {rest.strip()}")
                break
        else:
            parts.append(description[:56] + ("…" if len(description) > 56 else ""))
    return " · ".join(parts)


def label_from_column_value_mode(column: str, value: str, mode: str) -> str:
    name = short_column_name(column)
    if mode == "contains":
        return f'{name} contains "{value}"'
    return f"{name} = {value}"


def sanitize_csv_comment(text: str) -> str:
    """Make text safe for a leading '#' comment line in a CSV.

    Slashes are rewritten because Excel can misread them inside comment lines.
    """
    if not text:
        return ""
    return text.replace("/", "-")


def slug_for_filename(label: str, max_len: int = 80) -> str:
    """ASCII filename slug.

    AND / OR stay as the words "and" / "or". Parentheses and punctuation
    become underscores. "<=" and ">=" become "lte" and "gte" so the name
    stays readable on filesystems that dislike those characters.
    """
    ascii_label = (
        unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    )
    ascii_label = ascii_label.replace("<=", " lte ").replace(">=", " gte ")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_label).strip("_").lower()
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        slug = "query"
    if len(slug) <= max_len:
        return slug
    cut = slug[: max_len + 1]
    if "_" in cut:
        last = cut.rfind("_", 0, max_len)
        if last > 20:
            return cut[:last].rstrip("_")
    return slug[:max_len].rstrip("_")
