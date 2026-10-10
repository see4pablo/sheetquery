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

Useful commands while typing expressions: `BLANK` / `ESPACIO` (blank batch row), `PREVIOUS` / `ANTERIOR` (undo).

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
| `config/segmentation.conf` | Age bands and age/sex column headers |
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
