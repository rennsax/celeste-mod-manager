from pathlib import Path

from src import config, mod_manager
from src.path import get_mod_db_path, get_mods_dir


def test_mods_dir_fixture_configures_celeste_dir(mods_dir: Path):
    assert Path(config.CELESTE_DIR) == mods_dir.parent
    assert get_mods_dir() == mods_dir


def test_mod_paths_are_derived_from_celeste_dir(mods_dir: Path):
    assert get_mods_dir() == mods_dir
    assert get_mod_db_path("celeste_mod_db.wegfan.json") == (
        mods_dir / "celeste_mod_db.wegfan.json"
    )


def test_dummy_mod_zip_is_loaded_from_monkeypatched_mods_dir(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(mods_dir, "random-local-name.zip", "DummyMod", "1.2.3")

    mods = mod_manager.get_installed_mods()

    assert len(mods) == 1
    assert mods[0].name == "DummyMod"
    assert mods[0].version == "1.2.3"
    assert mods[0].get_filename() == "random-local-name.zip"
