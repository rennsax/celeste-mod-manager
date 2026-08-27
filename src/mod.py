import os
import sys
import yaml
import zipfile
from dataclasses import dataclass
from loguru import logger

from .operation import IssueKind, IssueSeverity, OperationIssue
from .path import get_mods_dir


@dataclass
class ModLoadResult:
    mod: "Mod | None"
    issues: list[OperationIssue]

    @property
    def ok(self) -> bool:
        return self.mod is not None


def _invalid_mod_issue(filename: str, detail: str) -> OperationIssue:
    return OperationIssue(
        severity=IssueSeverity.WARNING,
        kind=IssueKind.LOCAL_MOD_INVALID,
        operation="local mod scan",
        subject=filename,
        detail=detail,
    )


def _load_mod_cfg_detailed(filepath: str) -> tuple[dict | None, str | None]:
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
            if yaml_filename is None:
                return None, "missing everest.yaml or everest.yml"

            with zf.open(yaml_filename) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                return (
                    None,
                    "Everest metadata must be a non-empty list whose first entry is a mapping",
                )
            return data[0], None
    except zipfile.BadZipFile as e:
        return None, f"invalid ZIP archive: {e}"
    except yaml.YAMLError as e:
        return None, f"invalid Everest metadata: {e}"
    except (OSError, UnicodeError) as e:
        return None, f"failed to read archive: {e}"
    except Exception as e:
        logger.opt(exception=e).debug(f"Failed to read mod archive '{filepath}'.")
        return None, f"unexpected archive error: {e}"


def _load_mod_cfg(filepath: str) -> dict | None:
    """Read the metadata from specified mod file. The file is expected to be a
    zip file with the `everest.yml' / `everest.yaml' in the top.

    Return None if fails to read.
    """
    cfg, _ = _load_mod_cfg_detailed(filepath)
    return cfg


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

        filepath = str(get_mods_dir() / filename)

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

    @staticmethod
    def load_from_filename(filename: str) -> ModLoadResult:
        filepath = str(get_mods_dir() / filename)
        if not os.path.exists(filepath):
            return ModLoadResult(
                None, [_invalid_mod_issue(filename, "file does not exist")]
            )
        if not os.path.isfile(filepath) or not filepath.lower().endswith(".zip"):
            return ModLoadResult(None, [_invalid_mod_issue(filename, "not a ZIP file")])

        cfg, error = _load_mod_cfg_detailed(filepath)
        if cfg is None:
            return ModLoadResult(
                None,
                [
                    _invalid_mod_issue(
                        filename, error or "failed to load Everest metadata"
                    )
                ],
            )

        name = cfg.get("Name")
        version = cfg.get("Version")
        if not name or not version:
            return ModLoadResult(
                None,
                [
                    _invalid_mod_issue(
                        filename, "Everest metadata is missing Name or Version"
                    )
                ],
            )

        return ModLoadResult(
            Mod(name=str(name), version=str(version), filepath=filepath), []
        )

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
