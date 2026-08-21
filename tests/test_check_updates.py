from pathlib import Path
from types import SimpleNamespace

from src import mod_db, mod_manager
from src.cli import CelesteModCLI


def _installed_mod(mods_dir: Path, mod_zip_factory, filename: str, name: str):
    mod_zip_factory(mods_dir, filename, name, "1.0.0")
    mod = mod_manager.Mod.from_filename(filename)
    assert mod is not None
    return mod


def _mod_info(name: str, version: str, xx_hashes: list[str]):
    return SimpleNamespace(name=name, version=version, xxHash=xx_hashes)


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


def test_load_update_mod_index_forces_one_database_refresh(monkeypatch):
    calls = []
    mod_list = [{"name": "Current"}]
    expected_index = {"Current": object()}

    def fake_get_mod_db(url: str, force_update: bool = False):
        calls.append((url, force_update))
        return mod_list

    monkeypatch.setattr(mod_db, "get_mod_db", fake_get_mod_db)
    monkeypatch.setattr(mod_db, "index_mod_infos", lambda entries: expected_index)

    assert CelesteModCLI()._load_update_mod_index() is expected_index
    assert calls == [(f"{mod_db.config.WEGFAN_API_URL}/mod/list", True)]


def test_load_update_mod_index_warns_and_uses_cache_on_refresh_failure(
    monkeypatch, capsys
):
    cached_list = [{"name": "Cached"}]
    expected_index = {"Cached": object()}
    monkeypatch.setattr(
        mod_db,
        "get_mod_db",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(mod_db, "get_cached_mod_db", lambda: cached_list)
    monkeypatch.setattr(mod_db, "index_mod_infos", lambda entries: expected_index)

    assert CelesteModCLI()._load_update_mod_index() is expected_index
    assert capsys.readouterr().err == (
        "WARNING: failed to refresh the local mod database: offline. Using the "
        "existing cached database.\n"
    )


def test_load_update_mod_index_fails_without_usable_cache(monkeypatch, capsys):
    monkeypatch.setattr(
        mod_db,
        "get_mod_db",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(
        mod_db,
        "get_cached_mod_db",
        lambda: (_ for _ in ()).throw(ValueError("invalid cache")),
    )

    assert CelesteModCLI()._load_update_mod_index() is None
    assert capsys.readouterr().err == (
        "WARNING: failed to refresh the local mod database: offline. Using the "
        "existing cached database.\n"
        "ERROR: failed to load the local mod database cache: invalid cache\n"
    )


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
    (mods_dir / "blacklist.txt").write_text("Outdated.zip\n", encoding="utf-8")
    monkeypatch.setattr(
        mod_manager,
        "get_installed_mods",
        lambda: [excluded, outdated, current, unknown],
    )

    queried_names = []

    class TrackingIndex(dict):
        def get(self, name, default=None):
            queried_names.append(name)
            return super().get(name, default)

    mod_info_index = TrackingIndex(
        {
            "Current": _mod_info(
                "Current",
                "1.0.0",
                [mod_manager._calculate_xxhash64(current.filepath)],
            ),
            "Outdated": _mod_info("Outdated", "2.0.0", ["0" * 16]),
        }
    )
    monkeypatch.setattr(
        CelesteModCLI, "_load_update_mod_index", lambda _self: mod_info_index
    )

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
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {
            "Current": _mod_info(
                "Current",
                "1.0.0",
                [mod_manager._calculate_xxhash64(current.filepath)],
            )
        },
    )

    assert CelesteModCLI().check_updates([]) == 0

    output = capsys.readouterr().out
    assert "[OK]" in output
    assert (
        "Summary: total=1, outdated=0, up-to-date=1, skipped=0, blacklisted=0\n"
        in output
    )


def test_check_updates_warns_when_version_differs_but_hash_matches(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    mod_zip_factory(mods_dir, "LocalNewer.zip", "LocalNewer", "1.2.0")
    local_newer = mod_manager.Mod.from_filename("LocalNewer.zip")
    assert local_newer is not None
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [local_newer])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {
            "LocalNewer": _mod_info(
                "LocalNewer",
                "1.1.0",
                [mod_manager._calculate_xxhash64(local_newer.filepath)],
            )
        },
    )

    assert CelesteModCLI().check_updates([]) == 0

    output = capsys.readouterr().out
    assert "[OUTDATED]" not in output
    assert "[OK]" in output
    warning = (
        "\033[93m[WARNING]    \033[0m  LocalNewer  local=1.2.0  "
        "database=1.1.0; xxHash matches, treated as up to date\n"
    )
    assert warning in output
    assert output.index("[OK]") < output.index("[WARNING]") < output.rindex("-" * 72)
    assert "Summary: total=1, outdated=0, up-to-date=1" in output


def test_check_updates_sorts_version_warnings_at_end_of_table(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    zebra = _installed_mod(mods_dir, mod_zip_factory, "Zebra.zip", "Zebra")
    alpha = _installed_mod(mods_dir, mod_zip_factory, "Alpha.zip", "Alpha")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [zebra, alpha])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {
            mod.name: _mod_info(
                mod.name,
                "2.0.0",
                [mod_manager._calculate_xxhash64(mod.filepath)],
            )
            for mod in (zebra, alpha)
        },
    )

    assert CelesteModCLI().check_updates([]) == 0

    output = capsys.readouterr().out
    first_warning = output.index("[WARNING]")
    second_warning = output.index("[WARNING]", first_warning + 1)
    assert "Alpha" in output[first_warning:second_warning]
    assert "Zebra" in output[second_warning:]
    assert second_warning < output.rindex("-" * 72)
    assert "Summary: total=2, outdated=0, up-to-date=2, skipped=0" in output


def test_check_updates_skips_mod_without_valid_remote_hash(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [current])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Current": _mod_info("Current", "1.0.0", ["invalid"])},
    )

    assert CelesteModCLI().check_updates([]) == 0

    output = capsys.readouterr().out
    assert "[SKIP]" in output
    assert "remote hash unavailable" in output
    assert "Summary: total=1, outdated=0, up-to-date=0, skipped=1" in output


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
