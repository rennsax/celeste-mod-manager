from pathlib import Path

from src import config, mod_manager


def test_mods_dir_fixture_monkeypatches_config(mods_dir: Path):
    assert config.MODS_DIR == str(mods_dir)
    assert config._ENABLE_ROOT_INSTALL_TRACK is True


def test_dummy_mod_zip_is_loaded_from_monkeypatched_mods_dir(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(mods_dir, "random-local-name.zip", "DummyMod", "1.2.3")

    mods = mod_manager.get_installed_mods()

    assert len(mods) == 1
    assert mods[0].name == "DummyMod"
    assert mods[0].version == "1.2.3"
    assert mods[0].get_filename() == "random-local-name.zip"
