import zipfile
from pathlib import Path

import pytest
import yaml

from src import config, mod_manager


def make_mod_zip(
    mods_dir: Path,
    filename: str,
    name: str,
    version: str = "1.0.0",
    deps: list[dict[str, str]] | None = None,
    optional_deps: list[dict[str, str]] | None = None,
) -> Path:
    mod_path = mods_dir / filename
    cfg: dict = {
        "Name": name,
        "Version": version,
    }
    if deps is not None:
        cfg["Dependencies"] = deps
    if optional_deps is not None:
        cfg["OptionalDependencies"] = optional_deps

    with zipfile.ZipFile(mod_path, "w") as zf:
        zf.writestr("everest.yaml", yaml.safe_dump([cfg], sort_keys=False))

    return mod_path


def write_installed_mods(mods_dir: Path, roots: list[dict[str, str]]) -> Path:
    installed_mods_path = mods_dir / "installed_mods.yml"
    with installed_mods_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"root": roots}, f, sort_keys=False)
    return installed_mods_path


@pytest.fixture
def mods_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "MODS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_ENABLE_ROOT_INSTALL_TRACK", True)
    return tmp_path


@pytest.fixture
def mod_zip_factory():
    return make_mod_zip


@pytest.fixture
def installed_mods_writer():
    return write_installed_mods


@pytest.fixture
def no_network_download(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):
        pytest.fail("network/download should not be called in unit tests")

    monkeypatch.setattr(mod_manager, "_download_mod", fail_if_called)
    monkeypatch.setattr(mod_manager, "get_mod_info", fail_if_called)
