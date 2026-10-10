"""Age-band and sex breakdown used by query reports.

Bands, which columns hold age/sex, and whether each axis is enabled come from
config/segmentation.conf (see segmentation_config.py). Defaults match the
historical CESFAM tables: five-year groups from 15 up to 80 and over, plus sex.
"""

from __future__ import annotations

import re

import pandas as pd

from segmentation_config import SegmentationConfig, get_segmentation

# Source cells look like "68", "68,5", or "75 años 3 meses 27 días".
_AGE_YEARS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*a(?:ñ|n)os?", re.IGNORECASE)

MEN = "Men"
WOMEN = "Women"
OTHER_SEX = "Other / unspecified"
_SEX_COLUMNS = [MEN, WOMEN, OTHER_SEX]


def parse_age(series: pd.Series) -> pd.Series:
    """Return age in years.

    A plain number is used as-is. Text such as "75 años 3 meses 27 días"
    contributes only the year count; months and days are ignored.
    """
    text = series.astype(str).str.strip()
    direct = pd.to_numeric(text.str.replace(",", ".", regex=False), errors="coerce")
    extracted = text.str.extract(_AGE_YEARS_RE, expand=False)
    from_text = pd.to_numeric(
        extracted.str.replace(",", ".", regex=False),
        errors="coerce",
    )
    return direct.fillna(from_text)


def normalize_sex(series: pd.Series) -> pd.Series:
    """Map spreadsheet sex values to Men, Women, or Other / unspecified."""

    def one(value: str) -> str:
        token = re.sub(r"\s+", " ", str(value).strip()).upper()
        if token in ("MASCULINO", "M", "H", "HOMBRE", "VARÓN", "VARON", "MASC", "MALE"):
            return MEN
        if token in ("FEMENINO", "F", "MUJER", "FEM", "FEMALE"):
            return WOMEN
        return OTHER_SEX

    return series.astype(str).map(one)


def assign_age_group(
    age_years: pd.Series,
    *,
    config: SegmentationConfig | None = None,
) -> pd.Series:
    """Label each age with its band. Missing ages and ages under the configured start become NA."""
    cfg = config or get_segmentation()
    age_years = pd.to_numeric(age_years, errors="coerce")
    edges = cfg.age_range.edges()
    labels = cfg.age_range.labels()
    bands = pd.cut(
        age_years,
        bins=edges,
        labels=labels,
        right=False,
        ordered=True,
    )
    labeled = pd.Series(pd.NA, index=age_years.index, dtype=object)
    in_range = age_years.notna() & (age_years >= cfg.age_range.start)
    labeled.loc[in_range] = bands.loc[in_range].astype(str)
    return labeled


def prepare_dimensions(
    df: pd.DataFrame,
    age_column: str | None = None,
    sex_column: str | None = None,
    *,
    config: SegmentationConfig | None = None,
) -> pd.DataFrame:
    """Copy `df` and add `_age_num`, `_age_group`, and/or `_sex_norm` as configured."""
    cfg = config or get_segmentation()
    out = df.copy()
    if cfg.age_segmentation:
        if not age_column:
            raise ValueError("Age segmentation is on but no age column was provided.")
        out["_age_num"] = parse_age(out[age_column])
        out["_age_group"] = assign_age_group(out["_age_num"], config=cfg)
    if cfg.gender_segmentation:
        if not sex_column:
            raise ValueError(
                "Gender segmentation is on but no sex column was provided."
            )
        out["_sex_norm"] = normalize_sex(out[sex_column])
    return out


def age_sex_count_table(
    df: pd.DataFrame,
    *,
    age_column: str | None = None,
    sex_column: str | None = None,
    config: SegmentationConfig | None = None,
) -> pd.DataFrame:
    """Count rows by the configured segmentation axes.

    With the default config (age and gender on), rows are age bands and columns
    are Men / Women / Other. Only rows with a valid age band are counted when
    age segmentation is on.
    """
    cfg = config or get_segmentation()
    prepared = prepare_dimensions(
        df, age_column=age_column, sex_column=sex_column, config=cfg
    )

    if cfg.age_segmentation and cfg.gender_segmentation:
        valid = prepared.loc[prepared["_age_group"].notna()].copy()
        labels = cfg.age_range.labels()
        if valid.empty:
            return pd.DataFrame(0, index=list(labels), columns=_SEX_COLUMNS).astype(int)
        valid["_age_group"] = valid["_age_group"].astype(str)
        valid["_sex_norm"] = valid["_sex_norm"].astype(str)
        counts = (
            valid.groupby(["_age_group", "_sex_norm"], observed=False)
            .size()
            .unstack(fill_value=0)
        )
        for column in _SEX_COLUMNS:
            if column not in counts.columns:
                counts[column] = 0
        counts = counts[_SEX_COLUMNS].astype(int)
        counts = counts.reindex(labels, fill_value=0)
        counts.index.name = "Age group"
        counts.columns.name = None
        return counts

    if cfg.age_segmentation:
        valid = prepared.loc[prepared["_age_group"].notna()].copy()
        labels = cfg.age_range.labels()
        if valid.empty:
            return pd.DataFrame({"Count": 0}, index=list(labels)).astype(int)
        counts = (
            valid.groupby("_age_group", observed=False)
            .size()
            .reindex(labels, fill_value=0)
        )
        out = counts.to_frame(name="Count").astype(int)
        out.index.name = "Age group"
        return out

    if cfg.gender_segmentation:
        counts = prepared.groupby("_sex_norm", observed=False).size()
        row = {column: int(counts.get(column, 0)) for column in _SEX_COLUMNS}
        return pd.DataFrame([row], index=["All"]).astype(int)

    return pd.DataFrame({"Count": [len(prepared)]}, index=["All"]).astype(int)


def totals_summary(table: pd.DataFrame) -> dict[str, int]:
    """Sum a count table produced by age_sex_count_table."""
    summary = {"cell_total": int(table.to_numpy().sum())}
    if MEN in table.columns:
        summary["men_total"] = int(table[MEN].sum())
    if WOMEN in table.columns:
        summary["women_total"] = int(table[WOMEN].sum())
    if OTHER_SEX in table.columns:
        summary["other_sex_total"] = int(table[OTHER_SEX].sum())
    return summary


def filter_column_value(
    df: pd.DataFrame,
    column: str,
    value: str,
    *,
    mode: str = "normalized_equals",
) -> pd.Series:
    """Boolean mask for one text comparison.

    `normalized_equals` trims, collapses whitespace, and ignores case.
    `contains` is the same normalization, then a substring match. Use it when
    the cell has extra text around the value you care about.
    """
    normalized = (
        df[column]
        .astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.upper()
    )
    expected = re.sub(r"\s+", " ", value.strip()).upper()
    if mode == "contains":
        return normalized.str.contains(re.escape(expected), na=False)
    return normalized == expected


def age_group_labels(*, config: SegmentationConfig | None = None) -> list[str]:
    cfg = config or get_segmentation()
    return list(cfg.age_range.labels())


def _wide_group_label(group: str) -> str:
    """Drop the trailing " years" so wide headers read "15 to 19", not the full label."""
    label = str(group).strip()
    suffix = " years"
    if label.endswith(suffix):
        return label[: -len(suffix)].strip()
    return label


def wide_age_sex_men_women_table(
    long_table: pd.DataFrame,
    *,
    include_grand_totals: bool,
    config: SegmentationConfig | None = None,
) -> pd.DataFrame:
    """One count row shaped for CSV export.

    Default (age + gender): two-level header age band × sex (Men/Women only).
    Other segmentation modes return a single flat header row of counts.
    """
    cfg = config or get_segmentation()
    table = long_table.copy()
    table = table.drop(index="Total", errors="ignore")
    table = table.drop(columns="Total", errors="ignore")

    if cfg.age_segmentation and cfg.gender_segmentation:
        sexes = [MEN, WOMEN]
        table = table[[column for column in sexes if column in table.columns]]
        column_names: list[tuple[str, str]] = []
        values: list[int] = []
        for group in table.index:
            short_group = _wide_group_label(str(group))
            for sex in sexes:
                column_names.append((short_group, sex))
                values.append(int(table.loc[group, sex]))
        if include_grand_totals:
            column_names.extend([("Total", MEN), ("Total", WOMEN)])
            values.extend([int(table[MEN].sum()), int(table[WOMEN].sum())])
        columns = pd.MultiIndex.from_tuples(column_names, names=["Age group", "Sex"])
        return pd.DataFrame([values], columns=columns, index=["Count"]).astype(int)

    # Flat layout when only one axis (or neither) is enabled.
    if cfg.age_segmentation and "Count" in table.columns:
        values = [int(table.loc[group, "Count"]) for group in table.index]
        labels = [_wide_group_label(str(group)) for group in table.index]
        if include_grand_totals:
            labels.append("Total")
            values.append(int(sum(values)))
        return pd.DataFrame([values], columns=labels, index=["Count"]).astype(int)

    if cfg.gender_segmentation:
        sexes = [column for column in (MEN, WOMEN) if column in table.columns]
        values = [int(table.iloc[0][sex]) for sex in sexes]
        if include_grand_totals:
            sexes = [*sexes, "Total"]
            values.append(int(sum(values)))
        return pd.DataFrame([values], columns=sexes, index=["Count"]).astype(int)

    total = int(table.to_numpy().sum())
    return pd.DataFrame([[total]], columns=["Count"], index=["Count"]).astype(int)
