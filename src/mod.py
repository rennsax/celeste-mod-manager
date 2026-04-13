import os
import sys
import yaml
import zipfile
from typing import Optional, List
from loguru import logger

from .config import *

def load_mod_cfg(mod_file: str) -> Optional[dict]:
    filepath = os.path.join(MODS_DIR, mod_file)
    if not os.path.exists(filepath):
        logger.error(f"Mod file '{mod_file}' not found.")
        return None

    try:
        with zipfile.ZipFile(filepath, 'r') as zf:
            yaml_filename = next((name for name in zf.namelist() if name.lower() in ('everest.yaml', 'everest.yml')), None)
            if yaml_filename:
                with zf.open(yaml_filename) as f:
                    return yaml.safe_load(f)[0] # Assuming the YAML file contains a list and we want the first item
            else:
                logger.warning(f"No everest.yaml found in '{mod_file}'.")
                return None
    except Exception as e:
        logger.error(f"Failed to read from '{mod_file}': {e}")
        return None

# Represent a local mod.
class Mod:
    name: str
    version: str

    def __init__(self, name: str, version: str, subdir: str = ""):
        self.name = name
        self.version = version
        self.subdir = subdir

    # REVIEW: currently we initialize a Mod instance from a filename, but it
    # might be more robust to read the everest.yaml inside the zip to get
    # accurate metadata.
    @staticmethod
    def from_filename(filename: str, subdir: str = "") -> Optional['Mod']:
        filepath = os.path.join(MODS_DIR, subdir, filename)
        if not os.path.exists(filepath):
            logger.error(f"File '{filepath}' does not exist.")
            return None
        if filename.endswith(".zip"):
            name_version = filename[:-4].split('-', 1)
            if len(name_version) == 2:
                name, version = name_version
                return Mod(name=name, version=version, subdir=subdir)
        logger.error(f"Filename '{filename}' does not match expected format 'Name-Version.zip'.")
        return None

    def get_filename(self) -> str:
        return f"{self.name}-{self.version}.zip"

    def get_filepath(self) -> str:
        return os.path.join(MODS_DIR, self.subdir, self.get_filename())

    def __repr__(self):
        return f"Mod(name='{self.name}', version='{self.version}', subdir='{self.subdir}')"

    def load_everest_yaml(self) -> Optional[dict]:
        mod_file = self.get_filepath()
        return load_mod_cfg(mod_file)

    def get_mod_deps(self, optional: bool = False) -> List[dict[str, str]]:
        cfg = self.load_everest_yaml()
        if not cfg:
            logger.critical(f"Failed to load everest.yaml for mod '{self.get_filename()}'. Cannot determine dependencies.")
            sys.exit(1)
        else:
            return cfg.get('Dependencies', []) + (cfg.get('OptionalDependencies', []) if optional else [])

