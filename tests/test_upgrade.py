import sys
from pathlib import Path
from types import SimpleNamespace

from src import mod_manager
from src.cli import CelesteModCLI


def _mod_info(name: str, version: str, xx_hashes: list[str] | None = None):
    return SimpleNamespace(
        name=name,
        version=version,
        xxHash=xx_hashes if xx_hashes is not None else ["0" * 16],
        submissionFile=SimpleNamespace(
            url=f"https://example.invalid/{name}.zip",
            size=100,
        ),
    )


def _installed_mod(
    mods_dir: Path,
    mod_zip_factory,
    filename: str,
    name: str,
    version: str = "1.0.0",
):
    mod_zip_factory(mods_dir, filename, name, version)
    mod = mod_manager.Mod.from_filename(filename)
    assert mod is not None
    return mod


def _prepare_update(
    mods_dir: Path, mod_zip_factory, monkeypatch, new_version: str = "2.0.0"
):
    old_filename = "Example-1.0.0.zip"
    new_filename = f"Example-{new_version}.zip"
    mod_zip_factory(mods_dir, old_filename, "Example", "1.0.0")
    old_mod = mod_manager.Mod.from_filename(old_filename)
    assert old_mod is not None

    monkeypatch.setattr(
        mod_manager, "get_mod_info", lambda _name: _mod_info("Example", new_version)
    )

    def fake_download_mod(_mod_info):
        mod_zip_factory(mods_dir, new_filename, "Example", new_version)
        updated_mod = mod_manager.Mod.from_filename(new_filename)
        assert updated_mod is not None
        return updated_mod

    monkeypatch.setattr(mod_manager, "_download_mod", fake_download_mod)
    return old_mod, old_filename, new_filename


def test_update_mod_replaces_blacklisted_archive_filenames_in_place(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_mod, old_filename, new_filename = _prepare_update(
        mods_dir, mod_zip_factory, monkeypatch
    )
    blacklist_path = mods_dir / "blacklist.txt"
    blacklist_path.write_bytes(
        (
            b"# User-maintained blacklist\r\n"
            b"Other.zip\r\n"
            + f"  {old_filename}  \r\n".encode()
            + f"{old_filename}\r\n".encode()
            + f"# {old_filename}\r\n".encode()
        )
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / new_filename).exists()
    assert blacklist_path.read_bytes() == (
        b"# User-maintained blacklist\r\n"
        b"Other.zip\r\n"
        + f"  {new_filename}  \r\n".encode()
        + f"{new_filename}\r\n".encode()
        + f"# {old_filename}\r\n".encode()
    )
    assert old_filename not in mod_manager.get_blacklisted_mod_filenames()
    assert new_filename in mod_manager.get_blacklisted_mod_filenames()


def test_update_mod_replaces_ordered_archive_filenames_in_place(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_mod, old_filename, new_filename = _prepare_update(
        mods_dir, mod_zip_factory, monkeypatch
    )
    order_path = mods_dir / "modoptionsorder.txt"
    order_path.write_bytes(
        (
            b"# User-maintained order\r\n"
            b"Other.zip\r\n"
            + f"  {old_filename}  \r\n".encode()
            + f"{old_filename}\r\n".encode()
            + b"Everest\r\n"
        )
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert updated_mod.version == "2.0.0"
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / new_filename).exists()
    assert order_path.read_bytes() == (
        b"# User-maintained order\r\n"
        b"Other.zip\r\n"
        + f"  {new_filename}  \r\n".encode()
        + f"{new_filename}\r\n".encode()
        + b"Everest\r\n"
    )


def test_update_mod_does_not_create_missing_order_file(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_mod, old_filename, new_filename = _prepare_update(
        mods_dir, mod_zip_factory, monkeypatch
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert not (mods_dir / "modoptionsorder.txt").exists()
    assert not (mods_dir / "blacklist.txt").exists()
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / new_filename).exists()


def test_update_mod_does_not_rewrite_order_without_old_filename(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_mod, _, _ = _prepare_update(mods_dir, mod_zip_factory, monkeypatch)
    order_path = mods_dir / "modoptionsorder.txt"
    original_order = b"# User-maintained order\nOther.zip\nEverest\n"
    order_path.write_bytes(original_order)

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert order_path.read_bytes() == original_order


def test_update_mod_does_not_rewrite_blacklist_without_old_filename(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_mod, _, _ = _prepare_update(mods_dir, mod_zip_factory, monkeypatch)
    blacklist_path = mods_dir / "blacklist.txt"
    original_blacklist = b"# User-maintained blacklist\nOther.zip\n"
    blacklist_path.write_bytes(original_blacklist)

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert blacklist_path.read_bytes() == original_blacklist


def test_update_mod_leaves_order_unchanged_without_an_update(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_filename = "Example-1.0.0.zip"
    mod_zip_factory(mods_dir, old_filename, "Example", "1.0.0")
    old_mod = mod_manager.Mod.from_filename(old_filename)
    assert old_mod is not None
    order_path = mods_dir / "modoptionsorder.txt"
    original_order = f"{old_filename}\n".encode()
    order_path.write_bytes(original_order)
    blacklist_path = mods_dir / "blacklist.txt"
    original_blacklist = f"{old_filename}\n".encode()
    blacklist_path.write_bytes(original_blacklist)
    monkeypatch.setattr(
        mod_manager,
        "get_mod_info",
        lambda _name: _mod_info(
            "Example",
            "1.0.0",
            [mod_manager._calculate_xxhash64(old_mod.filepath)],
        ),
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE
    assert updated_mod == old_mod
    assert order_path.read_bytes() == original_order
    assert blacklist_path.read_bytes() == original_blacklist


def test_update_mod_accepts_any_catalog_hash_for_installed_archive(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    filename = "Example-1.0.0.zip"
    mod_zip_factory(mods_dir, filename, "Example", "1.0.0")
    installed_mod = mod_manager.Mod.from_filename(filename)
    assert installed_mod is not None
    local_xxhash = mod_manager._calculate_xxhash64(installed_mod.filepath)
    monkeypatch.setattr(
        mod_manager,
        "get_mod_info",
        lambda _name: _mod_info("Example", "2.0.0", ["0" * 16, local_xxhash]),
    )

    def fail_if_downloaded(_mod_info):
        raise AssertionError("a matching secondary catalog hash is current")

    monkeypatch.setattr(mod_manager, "_download_mod", fail_if_downloaded)

    updated_mod, status = mod_manager.update_mod(installed_mod)

    assert status == mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE
    assert updated_mod == installed_mod


def test_update_mod_treats_hash_as_authoritative_over_version_order(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_filename = "Example-1.2.0.zip"
    new_filename = "Example-1.1.0.zip"
    mod_zip_factory(mods_dir, old_filename, "Example", "1.2.0")
    installed_mod = mod_manager.Mod.from_filename(old_filename)
    assert installed_mod is not None
    monkeypatch.setattr(
        mod_manager, "get_mod_info", lambda _name: _mod_info("Example", "1.1.0")
    )

    def fake_download_mod(_mod_info):
        mod_zip_factory(mods_dir, new_filename, "Example", "1.1.0")
        return mod_manager.Mod.from_filename(new_filename)

    monkeypatch.setattr(mod_manager, "_download_mod", fake_download_mod)

    updated_mod, status = mod_manager.update_mod(installed_mod)

    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert updated_mod.version == "1.1.0"
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / new_filename).exists()


def test_update_mod_uses_downloaded_archive_version_when_database_is_stale(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    old_filename = "Example-1.0.0.zip"
    actual_filename = "Example-1.2.0.zip"
    mod_zip_factory(mods_dir, old_filename, "Example", "1.0.0")
    installed_mod = mod_manager.Mod.from_filename(old_filename)
    assert installed_mod is not None
    (mods_dir / "blacklist.txt").write_text(f"{old_filename}\n", encoding="utf-8")
    (mods_dir / "modoptionsorder.txt").write_text(f"{old_filename}\n", encoding="utf-8")
    source_dir = mods_dir / "source"
    source_dir.mkdir()
    source_path = mod_zip_factory(source_dir, "Example.zip", "Example", "1.2.0")
    expected_xxhash = mod_manager._calculate_xxhash64(str(source_path))
    monkeypatch.setattr(
        mod_manager,
        "get_mod_info",
        lambda _name: _mod_info("Example", "1.1.0", [expected_xxhash]),
    )

    def fake_urlretrieve(url, filepath, reporthook=None):
        Path(filepath).write_bytes(source_path.read_bytes())

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    updated_mod, status = mod_manager.update_mod(installed_mod)

    captured = capsys.readouterr()
    assert status == mod_manager.UpdateModStatus.UPDATED
    assert updated_mod is not None
    assert updated_mod.version == "1.2.0"
    assert updated_mod.get_filename() == actual_filename
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / actual_filename).exists()
    assert (mods_dir / "blacklist.txt").read_text(encoding="utf-8") == (
        f"{actual_filename}\n"
    )
    assert (mods_dir / "modoptionsorder.txt").read_text(encoding="utf-8") == (
        f"{actual_filename}\n"
    )
    assert "database reports version 1.1.0" in captured.err
    assert "celeste-mod-manager update-db" in captured.err


def test_update_mod_leaves_order_unchanged_when_download_fails(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    old_mod, _, _ = _prepare_update(mods_dir, mod_zip_factory, monkeypatch)
    order_path = mods_dir / "modoptionsorder.txt"
    original_order = b"Example-1.0.0.zip\n"
    order_path.write_bytes(original_order)
    blacklist_path = mods_dir / "blacklist.txt"
    original_blacklist = b"Example-1.0.0.zip\n"
    blacklist_path.write_bytes(original_blacklist)
    monkeypatch.setattr(mod_manager, "_download_mod", lambda _mod_info: None)

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert updated_mod is None
    assert status == mod_manager.UpdateModStatus.DOWNLOAD_FAILED
    assert order_path.read_bytes() == original_order
    assert blacklist_path.read_bytes() == original_blacklist


def test_update_mod_reports_checksum_failure_separately(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    old_filename = "Example-1.0.0.zip"
    mod_zip_factory(mods_dir, old_filename, "Example", "1.0.0")
    old_mod = mod_manager.Mod.from_filename(old_filename)
    assert old_mod is not None
    monkeypatch.setattr(
        mod_manager, "get_mod_info", lambda _name: _mod_info("Example", "2.0.0")
    )

    def fake_urlretrieve(url, filepath, reporthook=None):
        Path(filepath).write_bytes(b"unexpected complete download")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert updated_mod is None
    assert status == mod_manager.UpdateModStatus.CHECKSUM_FAILED
    assert "file integrity check failed for mod 'Example'" in capsys.readouterr().err
    assert (mods_dir / old_filename).exists()
    assert not list(mods_dir.glob("*.download.zip"))


def test_update_mod_requires_valid_catalog_hash_before_download(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    filename = "Example-1.0.0.zip"
    mod_zip_factory(mods_dir, filename, "Example", "1.0.0")
    installed_mod = mod_manager.Mod.from_filename(filename)
    assert installed_mod is not None
    monkeypatch.setattr(
        mod_manager,
        "get_mod_info",
        lambda _name: _mod_info("Example", "2.0.0", []),
    )

    def fail_if_downloaded(_mod_info):
        raise AssertionError("download must not start without a valid xxHash")

    monkeypatch.setattr(mod_manager, "_download_mod", fail_if_downloaded)

    updated_mod, status = mod_manager.update_mod(installed_mod)

    assert updated_mod is None
    assert status == mod_manager.UpdateModStatus.CHECKSUM_FAILED
    assert "cannot verify mod 'Example'" in capsys.readouterr().err


def test_update_mod_warns_and_continues_when_blacklist_update_fails(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    old_mod, old_filename, new_filename = _prepare_update(
        mods_dir, mod_zip_factory, monkeypatch
    )
    order_path = mods_dir / "modoptionsorder.txt"
    order_path.write_text(f"{old_filename}\n", encoding="utf-8")

    def fail_to_update_blacklist(_old_filename: str, _new_filename: str):
        raise OSError("disk full")

    monkeypatch.setattr(
        mod_manager, "_replace_blacklist_filename", fail_to_update_blacklist
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert updated_mod is not None
    assert status == mod_manager.UpdateModStatus.UPDATED
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / new_filename).exists()
    assert order_path.read_text(encoding="utf-8") == f"{new_filename}\n"
    assert capsys.readouterr().err == (
        "WARNING: failed to update blacklist from "
        f"'{old_filename}' to '{new_filename}': disk full.\n"
    )


def test_update_mod_warns_and_continues_when_order_update_fails(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    old_mod, old_filename, new_filename = _prepare_update(
        mods_dir, mod_zip_factory, monkeypatch
    )
    order_path = mods_dir / "modoptionsorder.txt"
    original_order = f"{old_filename}\n".encode()
    order_path.write_bytes(original_order)

    def fail_to_update_order(_old_filename: str, _new_filename: str):
        raise OSError("disk full")

    monkeypatch.setattr(
        mod_manager, "_replace_mod_options_order_filename", fail_to_update_order
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert updated_mod is not None
    assert status == mod_manager.UpdateModStatus.UPDATED
    assert not (mods_dir / old_filename).exists()
    assert (mods_dir / new_filename).exists()
    assert order_path.read_bytes() == original_order
    assert capsys.readouterr().err == (
        "WARNING: failed to update mod options order from "
        f"'{old_filename}' to '{new_filename}': disk full.\n"
    )


def test_upgrade_cli_reports_already_up_to_date_as_success(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    mod_zip_factory(mods_dir, "Example-1.0.0.zip", "Example", "1.0.0")
    installed_mod = mod_manager.Mod.from_filename("Example-1.0.0.zip")
    assert installed_mod is not None
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {
            "Example": _mod_info(
                "Example",
                "1.0.0",
                [mod_manager._calculate_xxhash64(installed_mod.filepath)],
            )
        },
    )

    exit_code = CelesteModCLI().upgrade(["Example"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "'Example' is already up to date.\n"
    assert captured.err == ""


def test_upgrade_cli_reports_successful_update(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    _prepare_update(mods_dir, mod_zip_factory, monkeypatch)
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Example": _mod_info("Example", "2.0.0")},
    )

    exit_code = CelesteModCLI().upgrade(["Example"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ("Successfully updated 'Example' from v1.0.0 to v2.0.0.\n\n")
    assert captured.err == ""


def test_upgrade_cli_reports_download_failure(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    mod_zip_factory(mods_dir, "Example-1.0.0.zip", "Example", "1.0.0")
    monkeypatch.setattr(
        mod_manager,
        "update_mod",
        lambda _mod, mod_info=None: (
            None,
            mod_manager.UpdateModStatus.DOWNLOAD_FAILED,
        ),
    )
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Example": _mod_info("Example", "2.0.0")},
    )

    exit_code = CelesteModCLI().upgrade(["Example"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == "ERROR: failed to download the update for mod 'Example'.\n"
    assert captured.err == ""


def test_upgrade_cli_does_not_relabel_checksum_failure_as_download_failure(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    mod_zip_factory(mods_dir, "Example-1.0.0.zip", "Example", "1.0.0")
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Example": _mod_info("Example", "2.0.0")},
    )

    def fake_update_mod(_mod, mod_info=None):
        print(
            "ERROR: file integrity check failed for mod 'Example'. Run "
            "'celeste-mod-manager update-db' and retry.",
            file=sys.stderr,
        )
        return None, mod_manager.UpdateModStatus.CHECKSUM_FAILED

    monkeypatch.setattr(mod_manager, "update_mod", fake_update_mod)

    exit_code = CelesteModCLI().upgrade(["Example"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "file integrity check failed" in captured.err
    assert "failed to download" not in captured.out + captured.err


def test_upgrade_cli_handles_missing_mod_for_updated_status(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    mod_zip_factory(mods_dir, "Example-1.0.0.zip", "Example", "1.0.0")
    monkeypatch.setattr(
        mod_manager,
        "update_mod",
        lambda _mod, mod_info=None: (None, mod_manager.UpdateModStatus.UPDATED),
    )
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Example": _mod_info("Example", "2.0.0")},
    )

    exit_code = CelesteModCLI().upgrade(["Example"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == (
        "ERROR: failed to update mod 'Example' due to an unexpected error.\n"
    )
    assert captured.err == ""


def test_upgrade_all_must_be_the_only_argument(monkeypatch, capsys):
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: (_ for _ in ()).throw(AssertionError("database loaded")),
    )

    assert CelesteModCLI().upgrade(["ALL", "Example"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: ALL must be the only argument to upgrade.\n"


def test_upgrade_lowercase_all_is_a_regular_mod_name(monkeypatch, capsys):
    monkeypatch.setattr(CelesteModCLI, "_load_update_mod_index", lambda _self: {})
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [])

    assert CelesteModCLI().upgrade(["all"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: mod 'all' is not installed. Cannot update a mod that is not "
        "installed.\n"
    )


def test_upgrade_all_reports_when_no_mods_are_installed(monkeypatch, capsys):
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: (_ for _ in ()).throw(AssertionError("database loaded")),
    )

    assert CelesteModCLI().upgrade(["ALL"]) == 0
    assert capsys.readouterr().out == "No mods installed.\n"


def test_upgrade_all_lists_and_updates_only_outdated_mods_in_name_order(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    alpha = _installed_mod(
        mods_dir, mod_zip_factory, "AlphaOutdated.zip", "AlphaOutdated"
    )
    blacklisted = _installed_mod(
        mods_dir, mod_zip_factory, "Blacklisted.zip", "Blacklisted"
    )
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    invalid = _installed_mod(mods_dir, mod_zip_factory, "Invalid.zip", "Invalid")
    unknown = _installed_mod(mods_dir, mod_zip_factory, "Unknown.zip", "Unknown")
    version_match = _installed_mod(
        mods_dir, mod_zip_factory, "VersionMatch.zip", "VersionMatch"
    )
    zeta = _installed_mod(mods_dir, mod_zip_factory, "ZetaOutdated.zip", "ZetaOutdated")
    (mods_dir / "updaterblacklist.txt").write_text(
        "Blacklisted.zip\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        mod_manager,
        "get_installed_mods",
        lambda: [zeta, unknown, version_match, invalid, current, blacklisted, alpha],
    )

    queried_names = []

    class TrackingIndex(dict):
        def get(self, name, default=None):
            queried_names.append(name)
            return super().get(name, default)

    alpha_info = _mod_info("AlphaOutdated", "2.0.0")
    zeta_info = _mod_info("ZetaOutdated", "3.0.0")
    mod_info_index = TrackingIndex(
        {
            "AlphaOutdated": alpha_info,
            "Current": _mod_info(
                "Current",
                "1.0.0",
                [mod_manager._calculate_xxhash64(current.filepath)],
            ),
            "Invalid": _mod_info("Invalid", "2.0.0", ["invalid"]),
            "VersionMatch": _mod_info(
                "VersionMatch",
                "2.0.0",
                [mod_manager._calculate_xxhash64(version_match.filepath)],
            ),
            "ZetaOutdated": zeta_info,
        }
    )
    monkeypatch.setattr(
        CelesteModCLI, "_load_update_mod_index", lambda _self: mod_info_index
    )
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "yes"
    )

    updated = []

    def fake_update_mod(mod, mod_info=None):
        updated.append((mod, mod_info))
        return mod, mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE

    monkeypatch.setattr(mod_manager, "update_mod", fake_update_mod)

    assert CelesteModCLI().upgrade(["ALL"]) == 0

    output = capsys.readouterr().out
    alpha_line = "  - AlphaOutdated (v1.0.0 -> v2.0.0) [AlphaOutdated.zip]\n"
    zeta_line = "  - ZetaOutdated (v1.0.0 -> v3.0.0) [ZetaOutdated.zip]\n"
    assert output.startswith("The following outdated mod(s) will be upgraded:\n")
    assert output.index(alpha_line) < output.index(zeta_line)
    for excluded_name in (
        "Blacklisted",
        "Current",
        "Invalid",
        "Unknown",
        "VersionMatch",
    ):
        assert f"  - {excluded_name} " not in output
    assert queried_names == [
        "AlphaOutdated",
        "Current",
        "Invalid",
        "Unknown",
        "VersionMatch",
        "ZetaOutdated",
    ]
    assert updated == [(alpha, alpha_info), (zeta, zeta_info)]
    assert prompts == ["Proceed? [y/N] "]


def test_upgrade_all_cancellation_does_not_update_mods(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    outdated = _installed_mod(mods_dir, mod_zip_factory, "Outdated.zip", "Outdated")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [outdated])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Outdated": _mod_info("Outdated", "2.0.0")},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr(
        mod_manager,
        "update_mod",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("updated")),
    )

    assert CelesteModCLI().upgrade(["ALL"]) == 0

    assert capsys.readouterr().out == (
        "The following outdated mod(s) will be upgraded:\n"
        "  - Outdated (v1.0.0 -> v2.0.0) [Outdated.zip]\n"
        "Skipped upgrading mods.\n"
    )


def test_upgrade_all_does_not_prompt_when_no_outdated_mods_exist(
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
                "2.0.0",
                [mod_manager._calculate_xxhash64(current.filepath)],
            )
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
    )

    assert CelesteModCLI().upgrade(["ALL"]) == 0
    assert capsys.readouterr().out == "No outdated mods found.\n"


def test_upgrade_all_continues_after_a_failed_update(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    alpha = _installed_mod(mods_dir, mod_zip_factory, "Alpha.zip", "Alpha")
    zeta = _installed_mod(mods_dir, mod_zip_factory, "Zeta.zip", "Zeta")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [zeta, alpha])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {
            "Alpha": _mod_info("Alpha", "2.0.0"),
            "Zeta": _mod_info("Zeta", "2.0.0"),
        },
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    updated_names = []

    def fake_update_mod(mod, mod_info=None):
        updated_names.append(mod.name)
        if mod.name == "Alpha":
            return None, mod_manager.UpdateModStatus.DOWNLOAD_FAILED
        return mod, mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE

    monkeypatch.setattr(mod_manager, "update_mod", fake_update_mod)

    assert CelesteModCLI().upgrade(["ALL"]) == 1

    output = capsys.readouterr().out
    assert updated_names == ["Alpha", "Zeta"]
    assert output.index("failed to download the update for mod 'Alpha'") < output.index(
        "'Zeta' is already up to date."
    )


def test_upgrade_all_aborts_when_update_blacklist_is_unreadable(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [current])
    monkeypatch.setattr(
        mod_manager,
        "get_update_blacklisted_mod_filenames",
        lambda: (_ for _ in ()).throw(OSError("permission denied")),
    )
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: (_ for _ in ()).throw(AssertionError("database loaded")),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
    )

    assert CelesteModCLI().upgrade(["ALL"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: failed to read update blacklist: permission denied\n"


def test_upgrade_all_aborts_when_mod_database_cannot_be_loaded(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    current = _installed_mod(mods_dir, mod_zip_factory, "Current.zip", "Current")
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [current])
    monkeypatch.setattr(CelesteModCLI, "_load_update_mod_index", lambda _self: None)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
    )

    assert CelesteModCLI().upgrade(["ALL"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_upgrade_all_skips_mod_when_local_hash_cannot_be_read(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    unreadable = _installed_mod(
        mods_dir, mod_zip_factory, "Unreadable.zip", "Unreadable"
    )
    monkeypatch.setattr(mod_manager, "get_installed_mods", lambda: [unreadable])
    monkeypatch.setattr(
        CelesteModCLI,
        "_load_update_mod_index",
        lambda _self: {"Unreadable": _mod_info("Unreadable", "2.0.0")},
    )
    monkeypatch.setattr(
        mod_manager,
        "_calculate_xxhash64",
        lambda _path: (_ for _ in ()).throw(OSError("disk error")),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
    )

    assert CelesteModCLI().upgrade(["ALL"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "No outdated mods found.\n"
    assert captured.err == (
        "WARNING: failed to calculate xxHash for mod 'Unreadable': disk error\n"
    )
