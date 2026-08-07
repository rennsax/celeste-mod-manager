from pathlib import Path
from types import SimpleNamespace

from src import mod_manager
from src.cli import CelesteModCLI


def _installed_mod(mods_dir: Path, mod_zip_factory, filename: str, name: str):
    mod_zip_factory(mods_dir, filename, name, "1.0.0")
    mod = mod_manager.Mod.from_filename(filename)
    assert mod is not None
    return mod


def test_get_update_blacklisted_mod_filenames_ignores_comments_and_blank_lines(
    mods_dir: Path,
):
    (mods_dir / "updaterblacklist.txt").write_text(
        "\n# Skip known incompatible updates\nExcluded-custom.zip\n  Other.zip  \n",
        encoding="utf-8",
    )

    assert mod_manager.get_update_blacklisted_mod_filenames() == {
        "Excluded-custom.zip",
        "Other.zip",
    }


def test_get_update_blacklisted_mod_filenames_returns_empty_when_file_is_missing(
    mods_dir: Path,
):
    assert mod_manager.get_update_blacklisted_mod_filenames() == set()


def test_check_updates_sorts_mods_and_skips_blacklisted_remote_lookups(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    excluded = _installed_mod(
        mods_dir, mod_zip_factory, "Excluded-custom.zip", "Excluded"
    )
    outdated = _installed_mod(mods_dir, mod_zip_factory, "Outdated.zip", "Outdated")
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    unknown = _installed_mod(mods_dir, mod_zip_factory, "Unknown.zip", "Unknown")
    (mods_dir / "updaterblacklist.txt").write_text(
        "# Archive names only\nExcluded\nExcluded-custom.zip\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        mod_manager,
        "get_installed_mods",
        lambda: [excluded, outdated, current, unknown],
    )

    queried_names = []

    def fake_get_mod_info(name: str):
        queried_names.append(name)
        if name == "Outdated":
            return SimpleNamespace(version="2.0.0")
        if name == "Current":
            return SimpleNamespace(version="1.0.0")
        if name == "Unknown":
            return None
        raise AssertionError(f"blacklisted mod '{name}' should not be queried")

    monkeypatch.setattr("src.cli.mod_db.get_mod_info", fake_get_mod_info)

    assert CelesteModCLI().check_updates([]) == 0

    assert queried_names == ["Current", "Outdated", "Unknown"]
    assert capsys.readouterr().out == (
        "-" * 72
        + "\n"
        + "Status         Mod       Version\n"
        + "-" * 72
        + "\n"
        + "\033[92m[OK]         \033[0m  Current   1.0.0\n"
        + "[BLACKLISTED]  Excluded  local=1.0.0  remote=not checked\n"
        + "\033[93m[OUTDATED]   \033[0m  Outdated  1.0.0 -> 2.0.0\n"
        + "[SKIP]         Unknown   local=1.0.0  remote=unknown\n"
        + "-" * 72
        + "\n"
        + "Summary: total=4, outdated=1, up-to-date=1, skipped=1, blacklisted=1\n"
    )


def test_check_updates_reports_zero_blacklisted_when_file_is_missing(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [current])
    monkeypatch.setattr(
        "src.cli.mod_db.get_mod_info", lambda _name: SimpleNamespace(version="1.0.0")
    )

    assert CelesteModCLI().check_updates([]) == 0

    output = capsys.readouterr().out
    assert "[OK]" in output
    assert (
        "Summary: total=1, outdated=0, up-to-date=1, skipped=0, blacklisted=0\n"
        in output
    )


def test_check_updates_reports_an_unreadable_blacklist(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [current])

    def fail_to_read_blacklist():
        raise OSError("permission denied")

    monkeypatch.setattr(
        mod_manager, "get_update_blacklisted_mod_filenames", fail_to_read_blacklist
    )

    assert CelesteModCLI().check_updates([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: failed to read update blacklist: permission denied\n"
