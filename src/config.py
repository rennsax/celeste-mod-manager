import os
import platform
import sys
from pathlib import Path
from typing import Optional
from .steam import steam_find_game

__all__ = [
    "WEGFAN_API_URL",
    "FORCE_UPDATE_DEFAULT",
    "DB_UPDATE_PERIOD_DAYS",
    "DEFAULT_LOG_LEVEL",
    "MOD_DB_PATH",
    "MODS_DIR"
]

def _get_celeste_dir() -> Optional[Path]:
    celeste_dir = steam_find_game(504230)
    if not celeste_dir:
        return None
    system = platform.system()
    if system == "Windows" or system == "Linux":
        return celeste_dir / "Mods"
    elif system == "Darwin":  # macOS
        return celeste_dir / "Celeste.app" / "Contents" / "Resources"

_CELESTE_DIR = _get_celeste_dir()
if not _CELESTE_DIR or not _CELESTE_DIR.exists():
    print("ERROR: Could not find Celeste installation directory. Please make sure Celeste is installed and try again.", file=sys.stderr)
    sys.exit(1)

WEGFAN_API_URL = "https://celeste.weg.fan/api/v2"
FORCE_UPDATE_DEFAULT = False
DB_UPDATE_PERIOD_DAYS = 7
DEFAULT_LOG_LEVEL = "DEBUG"
MOD_DB_PATH = os.path.join(_CELESTE_DIR, "celeste_mod_db.json")
MODS_DIR = os.path.join(_CELESTE_DIR, "Mods")

if __name__ == "__main__":
    print(f"Celeste installation directory: {_CELESTE_DIR}")
    print(f"Mod database path: {MOD_DB_PATH}")
    print(f"Mods directory: {MODS_DIR}")