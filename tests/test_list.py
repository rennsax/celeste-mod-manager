from pathlib import Path

from src import config
from src.cli import CelesteModCLI


def test_list_mods_prints_all_installed_mods(mods_dir: Path, mod_zip_factory, capsys):
    mod_zip_factory(mods_dir, "beta-custom-name.zip", "BetaMod", "2.0.0")
    mod_zip_factory(mods_dir, "alpha-custom-name.zip", "AlphaMod", "1.0.0")

    CelesteModCLI().list_mods([])

    assert capsys.readouterr().out == (
        "Mod      Version Enabled\n"
        "-------- ------- -------\n"
        "AlphaMod 1.0.0     ON   \n"
        "BetaMod  2.0.0     ON   \n"
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
        "Mod     Version Enabled\n"
        "------- ------- -------\n"
        "RootMod 1.0.0     ON   \n"
    )


def test_list_mods_root_only_uses_empty_root_mods_when_root_tracking_disabled(
    mods_dir: Path, monkeypatch, capsys
):
    monkeypatch.setattr(config, "_ENABLE_ROOT_INSTALL_TRACK", False)

    assert CelesteModCLI().list_mods(["--root"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "No mods installed.\n"
    assert captured.err == ""


def test_list_mods_marks_disabled_mods(mods_dir: Path, mod_zip_factory, capsys):
    mod_zip_factory(mods_dir, "EnabledMod.zip", "EnabledMod", "1.0.0")
    mod_zip_factory(mods_dir, "DisabledMod.zip", "DisabledMod", "1.0.0")
    (mods_dir / "blacklist.txt").write_text("DisabledMod.zip\n", encoding="utf-8")

    CelesteModCLI().list_mods([])

    assert capsys.readouterr().out == (
        "Mod         Version Enabled\n"
        "----------- ------- -------\n"
        "DisabledMod 1.0.0          \n"
        "EnabledMod  1.0.0     ON   \n"
    )


def test_list_mods_enabled_only_filters_disabled_mods(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "EnabledMod.zip", "EnabledMod", "1.0.0")
    mod_zip_factory(mods_dir, "DisabledMod.zip", "DisabledMod", "1.0.0")
    (mods_dir / "blacklist.txt").write_text("DisabledMod.zip\n", encoding="utf-8")

    CelesteModCLI().list_mods(["--enabled"])

    assert capsys.readouterr().out == (
        "Mod        Version\n" "---------- -------\n" "EnabledMod 1.0.0  \n"
    )


def test_list_mods_prints_no_mods_when_empty(mods_dir: Path, capsys):
    CelesteModCLI().list_mods([])

    assert capsys.readouterr().out == "No mods installed.\n"
