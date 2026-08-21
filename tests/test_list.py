from pathlib import Path

import pytest

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


def test_list_mods_rejects_removed_root_option(capsys):
    with pytest.raises(SystemExit) as exc_info:
        CelesteModCLI().list_mods(["--root"])

    assert exc_info.value.code == 2
    assert "no such option: --root" in capsys.readouterr().err


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
