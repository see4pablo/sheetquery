"""Resolve the application root when running from source or a frozen .exe."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Directory that holds config/, data/, queries/, and output/.

    For a PyInstaller build this is the folder of the .exe (not the temp unpack
    dir). For normal Python runs it is the current working directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def ensure_app_workdir() -> Path:
    """chdir to the app root so relative config/data/queries paths work."""
    root = app_root()
    os.chdir(root)
    return root
