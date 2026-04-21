import os
import platform
from pathlib import Path
from loguru import logger

from .steam import steam_find_game
from . import config


def get_celeste_dir() -> Path | None:
    """Try to find the Celeste installation directory. Return the path if found, or None if not found."""
    celeste_dir = steam_find_game(504230)
    if not celeste_dir:
        return None
    system = platform.system()
    if system == "Windows" or system == "Linux":
        return celeste_dir
    elif system == "Darwin":  # macOS
        return celeste_dir / "Celeste.app" / "Contents" / "Resources"


def set_mod_paths(celeste_dir: Path):
    config.MOD_DB_PATH = os.path.join(celeste_dir, "celeste_mod_db.json")
    config.MODS_DIR = os.path.join(celeste_dir, "Mods")
    config.BLACKLIST_PATH = os.path.join(config.MODS_DIR, "blacklist.txt")
