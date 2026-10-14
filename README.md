# SheetQuery

Python tool to filter spreadsheets (PSCV / CESFAM exports and similar) and produce counts by **age band** and **sex**, ready to paste into Excel.

## What it does

- Reads an Excel/CSV file (including cases where the real header starts several rows down).
- Applies query expressions such as `DIABETES = SI AND HBA1C < 8`.
- Writes `output/batch_*.csv` (one numeric row per query).
- Optionally writes one CSV per query (controlled by `SILENT_MODE`).

**Important:** workbooks often contain identifiable health data. Do not push them to public repositories or send them over insecure channels. The `data/` folder is gitignored on purpose.

## Requirements (development)

- Python 3.11+ (or the `shell.nix` environment)
- Dependencies: `pandas`, `openpyxl`, `numpy`, `pytest`

```bash
# With Nix
nix-shell
python -m pip install -r requirements.txt   # only if you are not using nixpkgs packages

# Or with a venv
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Three ways to use it

### 1. Interactive (day-to-day)

```bash
python interactive_query.py
```

1. Enter the **data** file (also looked up under `data/`).
2. Optionally enter a **queries** JSON file (empty Enter = type queries by hand).
3. If you did not use a queries file, the session is saved to `queries/queries_<timestamp>.json` when you finish.
4. Results go to `output/batch_*.csv`.

Useful commands while typing expressions: `BLANK` / `SPACE` / `ESPACIO` (empty line in the batch output, for layout), `PREVIOUS` / `ANTERIOR` (undo).

#### Example interactive session

Put a spreadsheet in `data/` (for example `yeco.csv`), then:

```text
$ python interactive_query.py
Data file: yeco.csv
Queries file (optional):          ← press Enter to type queries by hand

Expression: RCV = BAJO
Expression: DIABETES = SI
Expression: DIABETES = SI AND HBA1C < 8
Expression: ESPACIO               ← or SPACE / BLANK: empty line in the output (layout only)
Expression: HIPERTENSION = SI AND (PA SISTOLICA < 140 AND PA DIASTOLICA < 90)
Expression:                       ← empty Enter finishes the session
```

`ESPACIO`, `SPACE`, or `BLANK` does **not** run a filter. It writes an **empty line** in the final `batch_*.csv` so related blocks of results stay visually separated when you paste into a spreadsheet (ordering/layout only).

Example expressions you can type (cell values stay as in the workbook: `SI`, `NO`, `BAJO`, …):

| Expression | Meaning |
|---|---|
| `RCV = BAJO` | Cardiovascular risk = low |
| `DIABETES = SI` | Diabetes diagnosis = yes |
| `DIABETES = SI AND HBA1C < 8` | Diabetes with HbA1c under 8 |
| `HIPERTENSION = SI AND (PA SISTOLICA < 140 AND PA DIASTOLICA < 90)` | Hypertension with controlled BP |
| `ESPACIO` / `SPACE` / `BLANK` | Empty line in the output (separator for layout) |

When you finish, open `output/batch_*.csv`. That file is a **numeric grid** (one row per query, comma-separated counts by age band and sex) meant to be **pasted into Microsoft Excel, Google Sheets, LibreOffice Calc, or any similar spreadsheet**. Select the number rows (or open/import the CSV), paste into your sheet, and the values land in columns ready to match your reporting template.

#### Example output (`output/batch_….csv`)

Comment lines (`# …`) describe the file and column layout; they are not pasted as data. Each data line is one query: for every age band, two numbers (**M**en then **F**/Women), in order `15-19`, `20-24`, …, `80+`. The empty line after the third query comes from `ESPACIO` / `SPACE` / `BLANK` — it is only there to group rows when you paste.

```csv
# File: yeco.csv
# Session: 20261013_180000
# 15-19|20-24|25-29|30-34|35-39|40-44|45-49|50-54|55-59|60-64|65-69|70-74|75-79|80+
# M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F|M-F
2, 3, 1, 4, 5, 6, 8, 10, 12, 14, 9, 11, 7, 8, 6, 5, 4, 3, 2, 2, 1, 1, 0, 1, 0, 0, 0, 0
0, 1, 2, 3, 4, 5, 10, 12, 15, 18, 14, 16, 11, 13, 8, 9, 5, 6, 3, 4, 2, 2, 1, 1, 0, 1, 0, 0
0, 0, 1, 2, 2, 3, 6, 8, 9, 11, 8, 10, 6, 7, 4, 5, 2, 3, 1, 2, 1, 1, 0, 0, 0, 0, 0, 0

1, 2, 3, 4, 5, 7, 9, 11, 13, 15, 12, 14, 10, 11, 7, 8, 4, 5, 2, 3, 1, 2, 0, 1, 0, 0, 0, 0
```

In that sample, the blank line between the third and fourth number rows is what `ESPACIO` / `SPACE` / `BLANK` produces — useful to keep blocks apart in Excel or Sheets. How to read one cell pair: under `40-44` / `M-F`, the pair `8, 10` means 8 men and 10 women in that age band for that query. Numbers above are illustrative; your file will reflect the loaded spreadsheet.

### 2. Batch (no prompts)

```bash
python run_batch.py --data data/yeco.csv --queries queries/dani_y.json
```

With no arguments it uses `config/run.conf`:

- Empty `DATA_FILE=` → picks a file under `data/` (if several, the newest).
- `QUERIES_FILE=queries/dani_y.json` by default.

### 3. Windows package (for someone without Python)

The `.exe` **cannot be built on Linux**. Build it on a Windows PC:

```text
python -m pip install -r requirements.txt -r requirements-build.txt
python build_windows_exe.py
```

That creates `dist/SheetQuery/`. Zip and send that folder. End-user instructions: `packaging/WINDOWS_README.txt` (also copied into the zip).

For the person who receives it:

1. Put the spreadsheet in `data\`
2. Check `queries\dani_y.json` (or edit `config\run.conf`)
3. Double-click `SheetQuery.exe`
4. Open the `batch_....csv` in `output\`

## Configuration

| File | Purpose |
|------|---------|
| `config/app.conf` | `SILENT_MODE` (default `true`: batch only, no per-query CSV) |
| `config/run.conf` | Default data/queries for batch mode / the .exe |
| `config/column_aliases.conf` | Short names → spreadsheet headers (`DIABETES`, `RCV`, …) |
| `config/segmentation.conf` | Age bands, age/sex headers, and `DEDUP_COLUMN` (person key; default `Documento`, then `RUT`) |
| `*.conf.backup` | Snapshot of the default values |

Aliases and segmentation can be adapted per project without changing code.

## Example queries

Files under `queries/`:

- `dani_y.json` — PSCV query set (workbooks like `yeco.csv` / `sanjose.csv`)
- `queries.json` — editable session catalog

Format:

```json
{
  "queries": [
    "RCV = BAJO",
    "DIABETES = SI AND HBA1C < 8",
    "ESPACIO"
  ]
}
```

Cell values stay in the workbook language (`SI`, `NO`, `BAJO`, …).

## Tests

```bash
python -m pytest
```

## License

This project is released under the **MIT License** (see `LICENSE`).

You may use, modify, and share it. The main condition is to **keep the copyright notice and license text** in all copies or substantial portions of the Software—that is the required attribution.

If you redistribute the program (for example the Windows zip), include this `README` and the `LICENSE` file as well.

## Credits and AI use

- **Author:** Pablo  
- **Reference query set (`dani_y`):** prepared with / for CESFAM support use (credit to Dani for the query set).

**This project was developed with AI assistance** (AI-assisted coding tools, e.g. Cursor). Design choices, review, configuration data, and responsibility for how the software is used remain with the human author. The AI note lives in the README (not in the license) because the license governs legal permissions; the README is the usual place for transparency about how the code was built.
