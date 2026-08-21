import zipfile
from pathlib import Path

from src import mod_manager
from src.cli import CelesteModCLI


def test_scan_installed_mods_reports_invalid_zips_in_name_order(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(mods_dir, "Valid.zip", "Valid")
    (mods_dir / "Broken.zip").write_bytes(b"not a zip")
    with zipfile.ZipFile(mods_dir / "MissingMetadata.zip", "w") as zf:
        zf.writestr("readme.txt", "no Everest metadata")
    with zipfile.ZipFile(mods_dir / "InvalidMetadata.zip", "w") as zf:
        zf.writestr("everest.yaml", "not: [valid")
    with zipfile.ZipFile(mods_dir / "MissingFields.zip", "w") as zf:
        zf.writestr("everest.yaml", "- Name: MissingVersion\n")
    (mods_dir / "Ignored.txt").write_text("ignored", encoding="utf-8")
    (mods_dir / "Directory.zip").mkdir()

    result = mod_manager.scan_installed_mods()

    assert [mod.name for mod in result.mods] == ["Valid"]
    assert [issue.subject for issue in result.issues] == [
        "Broken.zip",
        "InvalidMetadata.zip",
        "MissingFields.zip",
        "MissingMetadata.zip",
    ]
    assert all(
        issue.kind == mod_manager.IssueKind.LOCAL_MOD_INVALID for issue in result.issues
    )


def test_list_warns_once_for_each_invalid_zip_and_continues(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "Valid.zip", "Valid")
    (mods_dir / "Broken.zip").write_bytes(b"not a zip")

    assert CelesteModCLI().list_mods([]) == 0

    captured = capsys.readouterr()
    assert "Valid   1.0.0" in captured.out
    assert captured.err.count("skipped local ZIP 'Broken.zip'") == 1


def test_apply_preserves_invalid_zip_blacklist_state(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "Root.zip", "Root")
    mod_zip_factory(mods_dir, "Other.zip", "Other")
    (mods_dir / "Broken.zip").write_bytes(b"not a zip")
    (mods_dir / "UnlistedBroken.zip").write_bytes(b"also not a zip")
    (mods_dir / "blacklist.txt").write_text("Broken.zip\n", encoding="utf-8")
    requirement_path = mods_dir / "requirements.txt"
    requirement_path.write_text("Root\n", encoding="utf-8")

    assert CelesteModCLI().apply(["-r", str(requirement_path)]) == 0

    captured = capsys.readouterr()
    assert captured.err.count("skipped local ZIP 'Broken.zip'") == 1
    assert captured.err.count("skipped local ZIP 'UnlistedBroken.zip'") == 1
    blacklist = mod_manager.get_blacklisted_mod_filenames()
    assert blacklist == {"Broken.zip", "Other.zip"}
