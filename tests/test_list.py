from pathlib import Path

from src.cli import CelesteModCLI


def test_list_mods_prints_all_installed_mods(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "beta-custom-name.zip", "BetaMod", "2.0.0")
    mod_zip_factory(mods_dir, "alpha-custom-name.zip", "AlphaMod", "1.0.0")

    CelesteModCLI().list_mods([])

    assert capsys.readouterr().out == (
        "Mod      Version\n"
        "-------- -------\n"
        "AlphaMod 1.0.0  \n"
        "BetaMod  2.0.0  \n"
    )


def test_list_mods_root_only_prints_recorded_root_mods(
    mods_dir: Path, mod_zip_factory, installed_mods_writer, capsys
):
    mod_zip_factory(mods_dir, "RootMod.zip", "RootMod", "1.0.0")
    mod_zip_factory(mods_dir, "DependencyMod.zip", "DependencyMod", "1.0.0")
    installed_mods_writer(
        mods_dir,
        [
            {
                "name": "RootMod",
                "version": "1.0.0",
                "filename": "RootMod.zip",
            }
        ],
    )

    CelesteModCLI().list_mods(["--root"])

    assert capsys.readouterr().out == (
        "Mod     Version\n"
        "------- -------\n"
        "RootMod 1.0.0  \n"
    )


def test_list_mods_prints_no_mods_when_empty(mods_dir: Path, capsys):
    CelesteModCLI().list_mods([])

    assert capsys.readouterr().out == "No mods installed.\n"
