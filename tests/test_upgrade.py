from pathlib import Path
from types import SimpleNamespace

from src import mod_manager


def _mod_info(name: str, version: str):
    return SimpleNamespace(name=name, version=version)


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
        mod_manager, "get_mod_info", lambda _name: _mod_info("Example", "1.0.0")
    )

    updated_mod, status = mod_manager.update_mod(old_mod)

    assert status == mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE
    assert updated_mod == old_mod
    assert order_path.read_bytes() == original_order
    assert blacklist_path.read_bytes() == original_blacklist


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
