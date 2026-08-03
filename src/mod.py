import os
import sys
import yaml
import zipfile
from loguru import logger

from . import config


def _load_mod_cfg(filepath: str) -> dict | None:
    """Read the metadata from specified mod file. The file is expected to be a
    zip file with the `everest.yml' / `everest.yaml' in the top.

    Return None if fails to read.
    """
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            yaml_filename = next(
                (
                    name
                    for name in zf.namelist()
                    if name.lower() in ("everest.yaml", "everest.yml")
                ),
                None,
            )
            if yaml_filename:
                with zf.open(yaml_filename) as f:
                    data = yaml.safe_load(f)
                    if (
                        not isinstance(data, list)
                        or not data
                        or not isinstance(data[0], dict)
                    ):
                        logger.debug(
                            f"Unexpected everest.yaml format in '{filepath}'."
                        )
                        return None
                    return data[0]
            else:
                logger.debug("No everest.yaml found in '{filepath}'.")
                return None
    except Exception as e:
        logger.debug(f"Failed to read from '{filepath}': {e}")
        return None


# Represent a local mod.
class Mod:
    name: str
    version: str
    filepath: str

    def __init__(self, name: str, version: str, filepath: str):
        self.name = name
        self.version = version
        self.filepath = filepath

    @staticmethod
    def from_filename(filename: str) -> "Mod | None":

        """
        From a ZIP-format mod file, initialize an internal mod structure.

        Return None if the initialization fails. It may be caused by:
        - An invalid file path. This is unexpected, so error message is logged.
        - Fail to load the metadata. This might happen because we cannot control
          which file(s) the users put into Mods/ directory.
        - Missing name or version in the metadata. This causes the mod unreadable
          for Everest too.
        """

        filepath = os.path.join(config.MODS_DIR, filename)

        if not os.path.exists(filepath):
            logger.error(f"File '{filepath}' does not exist.")
            return None

        if not os.path.isfile(filepath) or not filepath.lower().endswith(".zip"):
            logger.error(f"File '{filepath}' is not a zip mod file.")
            return None

        cfg = _load_mod_cfg(filepath)
        if not cfg:
            return None

        name = cfg.get("Name")
        version = cfg.get("Version")
        if not name or not version:
            logger.info(f"Missing Name or Version in '{filepath}'.")
            return None

        return Mod(name=str(name), version=str(version), filepath=filepath)

    def get_filename(self) -> str:
        return os.path.basename(self.filepath)

    def get_filepath(self) -> str:
        return self.filepath

    def __repr__(self):
        return (
            f"Mod(name='{self.name}', version='{self.version}', "
            f"filepath='{self.filepath}')"
        )

    def load_everest_yaml(self) -> dict | None:
        return _load_mod_cfg(self.filepath)

    def get_mod_deps(self, optional: bool = False) -> list[dict[str, str]]:
        cfg = self.load_everest_yaml()
        if not cfg:
            logger.critical(
                f"Failed to load everest.yaml for mod '{self.filepath}'. Cannot determine dependencies."
            )
            sys.exit(1)
        else:
            return cfg.get("Dependencies", []) + (
                cfg.get("OptionalDependencies", []) if optional else []
            )
