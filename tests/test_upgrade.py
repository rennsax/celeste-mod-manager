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
