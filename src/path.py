import os
import platform
from pathlib import Path

from loguru import logger

from . import config
from .steam import steam_find_game


class CelestePathError(Exception):
    """An expected, user-actionable Celeste path configuration error."""


def find_celeste_dir_from_steam() -> Path | None:
    """Find the Celeste installation directory from Steam, or return None."""
    celeste_dir = steam_find_game(504230)
    if not celeste_dir:
        return None
    system = platform.system()
    if system == "Windows" or system == "Linux":
        return celeste_dir
    elif system == "Darwin":  # macOS
        return celeste_dir / "Celeste.app" / "Contents" / "Resources"


def _validate_celeste_dir(candidate: Path, source: str) -> Path:
    try:
        candidate = candidate.expanduser().resolve()
        if not candidate.is_dir():
            raise CelestePathError(
                f"{source} '{candidate}' does not exist or is not a directory."
            )
        with os.scandir(candidate):
            pass
        if (
            not (candidate / "Celeste.exe").is_file()
            and not (candidate / "Celeste.dll").is_file()
        ):
            raise CelestePathError(
                f"{source} '{candidate}' is not a valid Celeste installation: "
                "neither Celeste.exe nor Celeste.dll was found."
            )
    except CelestePathError:
        raise
    except (OSError, RuntimeError) as e:
        raise CelestePathError(f"cannot access {source} '{candidate}': {e}.") from e
    return candidate


def configure_celeste_dir(cli_override: Path | None = None) -> Path:
    """Select, validate, and store the effective Celeste installation directory."""
    if cli_override is not None:
        candidate = cli_override
        source = "specified Celeste directory"
        logger.debug(f"Using Celeste directory from command line: {candidate}")
    elif config.CELESTE_DIR:
        candidate = Path(config.CELESTE_DIR)
        source = "configured CELESTE_DIR"
        logger.debug(f"Using configured Celeste directory: {candidate}")
    else:
        try:
            discovered = find_celeste_dir_from_steam()
        except Exception as e:
            raise CelestePathError(
                f"failed to automatically detect the Celeste installation: {e}."
            ) from e
        if discovered is None:
            raise CelestePathError(
                "Could not find Celeste installation directory. "
                "Please make sure Celeste is installed."
            )
        candidate = discovered
        source = "automatically detected Celeste directory"
        logger.debug(f"Using automatically detected Celeste directory: {candidate}")

    configured = _validate_celeste_dir(Path(candidate), source)
    config.CELESTE_DIR = str(configured)
    return configured


def get_configured_celeste_dir() -> Path:
    if not config.CELESTE_DIR:
        raise CelestePathError(
            "Celeste installation directory has not been configured."
        )
    return Path(config.CELESTE_DIR)


def get_mods_dir() -> Path:
    return get_configured_celeste_dir() / "Mods"


def get_mod_db_path() -> Path:
    return get_mods_dir() / "celeste_mod_db.json"


def validate_mods_dir() -> Path:
    mods_dir = get_mods_dir()
    try:
        if not mods_dir.exists():
            raise CelestePathError(
                f"Mods directory '{mods_dir}' does not exist. Everest may not be "
                "installed or may be damaged. Install or repair it with "
                "'celeste-mod-manager everest'."
            )
        if not mods_dir.is_dir():
            raise CelestePathError(
                f"invalid Mods path '{mods_dir}': expected a directory. Everest may "
                "not be installed or may be damaged. Install or repair it with "
                "'celeste-mod-manager everest'."
            )
        with os.scandir(mods_dir):
            pass
    except CelestePathError:
        raise
    except OSError as e:
        raise CelestePathError(f"cannot read Mods directory '{mods_dir}': {e}.") from e
    return mods_dir


def validate_mod_db_path() -> Path:
    mod_db_path = get_mod_db_path()
    try:
        if mod_db_path.exists() and not mod_db_path.is_file():
            entry_type = (
                "a directory" if mod_db_path.is_dir() else "a non-regular entry"
            )
            raise CelestePathError(
                f"invalid mod database path '{mod_db_path}': expected a file, "
                f"found {entry_type}."
            )
    except CelestePathError:
        raise
    except OSError as e:
        raise CelestePathError(
            f"cannot access mod database path '{mod_db_path}': {e}."
        ) from e
    return mod_db_path
