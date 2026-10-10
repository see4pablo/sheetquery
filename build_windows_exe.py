#!/usr/bin/env python3
"""Build a Windows folder your friend can use without installing Python.

Run this script ON Windows (PyInstaller cannot cross-compile from Linux/macOS):

  python -m pip install -r requirements.txt pyinstaller
  python build_windows_exe.py

Result (folder you can zip and send):
  dist/SheetQuery/
    SheetQuery.exe     ← double-click
    config/            ← app, segmentation, column aliases, run defaults
    data/              ← she drops the spreadsheet here
    queries/dani_y.json
    output/            ← batch results appear here
    WINDOWS_README.txt ← short end-user instructions
    LICENSE, README.md
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_NAME = "SheetQuery"
ENTRY = ROOT / "run_batch.py"


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if sys.platform != "win32":
        _fail(
            "This build must run on Windows.\n"
            "PyInstaller cannot produce a working Windows .exe from Linux/macOS.\n"
            "On a Windows PC with Python installed:\n"
            "  python -m pip install -r requirements.txt pyinstaller\n"
            "  python build_windows_exe.py"
        )

    if not ENTRY.is_file():
        _fail(f"Missing entry script: {ENTRY}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _fail(
            "PyInstaller is not installed.\n" "Run:  python -m pip install pyinstaller"
        )

    dist_root = ROOT / "dist" / DIST_NAME
    build_dir = ROOT / "build" / "pyinstaller"
    if dist_root.exists():
        shutil.rmtree(dist_root)
    dist_root.mkdir(parents=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        DIST_NAME,
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        # Console app so progress and errors are visible.
        "--console",
        str(ENTRY),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)

    # PyInstaller writes dist/SheetQuery/SheetQuery.exe (+ internals).
    # Copy companion folders next to the exe so paths stay relative.
    exe_dir = ROOT / "dist" / DIST_NAME
    if not (exe_dir / f"{DIST_NAME}.exe").is_file():
        _fail(f"Expected exe not found under {exe_dir}")

    # config/
    config_src = ROOT / "config"
    config_dst = exe_dir / "config"
    if config_dst.exists():
        shutil.rmtree(config_dst)
    shutil.copytree(
        config_src,
        config_dst,
        ignore=shutil.ignore_patterns("*.tmp"),
    )

    # queries/ — ship dani_y.json (and queries.json if present) as starters
    queries_dst = exe_dir / "queries"
    queries_dst.mkdir(exist_ok=True)
    for name in ("dani_y.json", "queries.json"):
        src = ROOT / "queries" / name
        if src.is_file():
            shutil.copy2(src, queries_dst / name)

    # data/ — empty placeholder (do not ship patient spreadsheets)
    data_dst = exe_dir / "data"
    data_dst.mkdir(exist_ok=True)
    (data_dst / "PUT_YOUR_SPREADSHEET_HERE.txt").write_text(
        "Copy your spreadsheet (.csv or .xlsx) into this folder.\n"
        "Edit config\\run.conf if you want a fixed DATA_FILE name.\n"
        "Otherwise the program uses the spreadsheet it finds here.\n",
        encoding="utf-8",
    )

    (exe_dir / "output").mkdir(exist_ok=True)

    readme_src = ROOT / "packaging" / "WINDOWS_README.txt"
    if readme_src.is_file():
        shutil.copy2(readme_src, exe_dir / "WINDOWS_README.txt")
    else:
        (exe_dir / "WINDOWS_README.txt").write_text(
            "1) Put the Excel/CSV in the data\\ folder\n"
            "2) Check queries\\dani_y.json\n"
            "3) Double-click SheetQuery.exe\n"
            "4) Open the new file in output\\\n",
            encoding="utf-8",
        )

    for name in ("LICENSE", "README.md"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, exe_dir / name)

    print()
    print("Build OK.")
    print(f"Folder to zip and send: {exe_dir}")
    print(
        "End user: put the spreadsheet in data\\, double-click the .exe, open output\\."
    )


if __name__ == "__main__":
    main()
