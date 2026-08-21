import io
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import xxhash

from src import mod_manager


def _dep(name: str, version: str = "1.0.0") -> dict[str, str]:
    return {"Name": name, "Version": version}


def _mod_info(
    name: str,
    version: str = "1.0.0",
    size: int = 100,
    xx_hashes: list[str] | None = None,
):
    return SimpleNamespace(
        name=name,
        version=version,
        xxHash=xx_hashes if xx_hashes is not None else ["0" * 16],
        submissionFile=SimpleNamespace(
            url=f"https://example.invalid/{name}.zip",
            size=size,
        ),
    )


def test_calculate_xxhash64_uses_everest_xxhash64_variant(tmp_path: Path):
    empty_file = tmp_path / "empty.zip"
    empty_file.write_bytes(b"")

    assert mod_manager._calculate_xxhash64(str(empty_file)) == "ef46db3751d8e999"


def test_download_mod_prints_progress(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    def fake_urlretrieve(url, filepath, reporthook=None):
        if reporthook:
            reporthook(0, 50, 100)
            reporthook(1, 50, 100)
            reporthook(2, 50, 100)
        path = Path(filepath)
        mod_zip_factory(path.parent, path.name, "DownloadedMod", "1.0.0")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(mod_manager, "_calculate_xxhash64", lambda _path: "0" * 16)

    result = mod_manager._download_mod(_mod_info("DownloadedMod"))
    mod = result.mod

    output = capsys.readouterr().out
    assert mod is not None
    assert mod.name == "DownloadedMod"
    collecting = output.index("Collecting DownloadedMod\n")
    downloading = output.index("  Downloading DownloadedMod-1.0.0.zip (100 B)\n")
    completed = output.index("100% 100 B/100 B")
    saved = output.index("  Saved DownloadedMod-1.0.0.zip\n")
    assert collecting < downloading < completed < saved
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_progress_uses_ascii_bar_for_gbk_stdout(monkeypatch):
    output_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(output_bytes, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stdout)

    with mod_manager._download_progress(expected_size=100) as reporthook:
        reporthook(1, 50, 100)
    stdout.flush()

    output = output_bytes.getvalue().decode("gbk")
    assert output.isascii()
    assert "-" * 15 in output
    assert "50% 50 B/100 B" in output
    assert "━" not in output
    assert "╸" not in output
    assert "╺" not in output


def test_download_progress_uses_unicode_bar_for_utf8_stdout(monkeypatch):
    output_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(output_bytes, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", stdout)

    with mod_manager._download_progress(expected_size=100) as reporthook:
        reporthook(1, 50, 100)
    stdout.flush()

    output = output_bytes.getvalue().decode("utf-8")
    assert "━" * 15 in output
    assert "50% 50 B/100 B" in output


def test_download_progress_handles_unknown_size_and_caps_at_total(capsys):
    with mod_manager._download_progress() as reporthook:
        reporthook(1, 50, -1)

    unknown_output = capsys.readouterr().out
    assert "Downloaded 50 B" in unknown_output

    with mod_manager._download_progress(expected_size=100) as reporthook:
        reporthook(3, 50, 100)

    completed_output = capsys.readouterr().out
    assert "100% 100 B/100 B" in completed_output


def test_download_mod_retries_then_publishes_valid_archive(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    attempts = 0

    def fake_urlretrieve(url, filepath, reporthook=None):
        nonlocal attempts
        attempts += 1
        if reporthook:
            reporthook(1, 50, 100)
        if attempts == 1:
            raise urllib.error.URLError("temporary network failure")
        if reporthook:
            reporthook(2, 50, 100)
        path = Path(filepath)
        mod_zip_factory(path.parent, path.name, "DownloadedMod", "1.0.0")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(mod_manager, "_calculate_xxhash64", lambda _path: "0" * 16)

    result = mod_manager._download_mod(_mod_info("DownloadedMod"))
    mod = result.mod

    assert attempts == 2
    assert mod is not None
    assert mod.filepath == str(mods_dir / "DownloadedMod-1.0.0.zip")
    assert mod_manager.Mod.from_filename("DownloadedMod-1.0.0.zip") is not None
    assert not list(mods_dir.glob("*.download.zip"))
    output = capsys.readouterr().out
    assert output.index("50% 50 B/100 B") < output.index("100% 100 B/100 B")


def test_download_mod_retries_content_too_short(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    attempts = 0

    def fake_urlretrieve(url, filepath, reporthook=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.ContentTooShortError("incomplete download", None)
        path = Path(filepath)
        mod_zip_factory(path.parent, path.name, "DownloadedMod", "1.0.0")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(mod_manager, "_calculate_xxhash64", lambda _path: "0" * 16)

    result = mod_manager._download_mod(_mod_info("DownloadedMod"))
    mod = result.mod

    assert attempts == 2
    assert mod is not None
    assert (mods_dir / "DownloadedMod-1.0.0.zip").exists()
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_rejects_invalid_archive_without_retry(
    mods_dir: Path, monkeypatch
):
    attempts = 0
    invalid_contents = b"not a zip file"
    expected_xxhash = xxhash.xxh64(invalid_contents).hexdigest()

    def fake_urlretrieve(url, filepath, reporthook=None):
        nonlocal attempts
        attempts += 1
        Path(filepath).write_bytes(invalid_contents)

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    result = mod_manager._download_mod(
        _mod_info("DownloadedMod", xx_hashes=[expected_xxhash])
    )

    assert attempts == 1
    assert result.mod is None
    assert result.issues[0].kind == mod_manager.IssueKind.ARCHIVE_INVALID
    assert not (mods_dir / "DownloadedMod-1.0.0.zip").exists()
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_failure_preserves_existing_archive(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    mod_zip_factory(mods_dir, "DownloadedMod-1.0.0.zip", "ExistingMod", "0.9.0")
    original_contents = (mods_dir / "DownloadedMod-1.0.0.zip").read_bytes()

    def fake_urlretrieve(url, filepath, reporthook=None):
        raise urllib.error.URLError("temporary network failure")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    result = mod_manager._download_mod(_mod_info("DownloadedMod"))

    assert result.mod is None
    assert result.issues[0].kind == mod_manager.IssueKind.DOWNLOAD_FAILED
    assert (mods_dir / "DownloadedMod-1.0.0.zip").read_bytes() == original_contents
    assert (
        mod_manager.Mod.from_filename("DownloadedMod-1.0.0.zip").name == "ExistingMod"
    )
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_checksum_failure_does_not_retry(
    mods_dir: Path, monkeypatch, capsys
):
    attempts = 0

    def fake_urlretrieve(url, filepath, reporthook=None):
        nonlocal attempts
        attempts += 1
        Path(filepath).write_bytes(b"complete but unexpected contents")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    result = mod_manager._download_mod(_mod_info("DownloadedMod"))

    captured = capsys.readouterr()
    actual_xxhash = xxhash.xxh64(b"complete but unexpected contents").hexdigest()
    assert attempts == 1
    assert captured.err == ""
    assert result.mod is None
    assert result.issues[0].kind == mod_manager.IssueKind.CHECKSUM_FAILED
    assert f"got '{actual_xxhash}'" in result.issues[0].detail
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_requires_primary_xxhash_before_network(
    mods_dir: Path, monkeypatch, capsys
):
    def fail_if_downloaded(*args, **kwargs):
        raise AssertionError("download must not start without a primary xxHash")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fail_if_downloaded)

    result = mod_manager._download_mod(_mod_info("DownloadedMod", xx_hashes=[]))

    assert capsys.readouterr().err == ""
    assert result.mod is None
    assert result.issues[0].kind == mod_manager.IssueKind.CHECKSUM_FAILED
    assert "does not contain a valid expected xxHash" in result.issues[0].detail
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_verifies_only_primary_xxhash(mods_dir: Path, monkeypatch):
    contents = b"matches only the secondary hash"
    actual_xxhash = xxhash.xxh64(contents).hexdigest()

    def fake_urlretrieve(url, filepath, reporthook=None):
        Path(filepath).write_bytes(contents)

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    result = mod_manager._download_mod(
        _mod_info("DownloadedMod", xx_hashes=["0" * 16, actual_xxhash])
    )

    assert result.mod is None
    assert result.issues[0].kind == mod_manager.IssueKind.CHECKSUM_FAILED
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_uses_archive_version_when_database_is_stale(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    source_dir = mods_dir / "source"
    source_dir.mkdir()
    source_path = mod_zip_factory(
        source_dir, "DownloadedMod.zip", "DownloadedMod", "1.2.0"
    )
    expected_xxhash = mod_manager._calculate_xxhash64(str(source_path))

    def fake_urlretrieve(url, filepath, reporthook=None):
        Path(filepath).write_bytes(source_path.read_bytes())

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    result = mod_manager._download_mod(
        _mod_info("DownloadedMod", version="1.1.0", xx_hashes=[expected_xxhash])
    )
    mod = result.mod

    captured = capsys.readouterr()
    assert mod is not None
    assert mod.version == "1.2.0"
    assert mod.filepath == str(mods_dir / "DownloadedMod-1.2.0.zip")
    assert not (mods_dir / "DownloadedMod-1.1.0.zip").exists()
    assert (mods_dir / "DownloadedMod-1.2.0.zip").exists()
    assert "  Downloading DownloadedMod-1.1.0.zip" in captured.out
    assert "  Saved DownloadedMod-1.2.0.zip\n" in captured.out
    assert captured.err == ""
    assert result.issues[0].kind == mod_manager.IssueKind.DATABASE_VERSION_MISMATCH
    assert not list(mods_dir.glob("*.download.zip"))


def test_download_mod_rejects_archive_with_different_mod_name(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    attempts = 0

    def fake_urlretrieve(url, filepath, reporthook=None):
        nonlocal attempts
        attempts += 1
        path = Path(filepath)
        mod_zip_factory(path.parent, path.name, "DifferentMod", "1.0.0")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(mod_manager, "_calculate_xxhash64", lambda _path: "0" * 16)

    result = mod_manager._download_mod(_mod_info("DownloadedMod"))

    assert attempts == 1
    assert result.mod is None
    assert result.issues[0].kind == mod_manager.IssueKind.ARCHIVE_INVALID
    assert not (mods_dir / "DownloadedMod-1.0.0.zip").exists()
    assert not (mods_dir / "DifferentMod-1.0.0.zip").exists()
    assert not list(mods_dir.glob("*.download.zip"))


def test_resolve_deps_prints_dependency_progress(
    mods_dir: Path, mod_zip_factory, monkeypatch, capsys
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[_dep("InstalledDependency"), _dep("DownloadedDependency")],
    )
    mod_zip_factory(
        mods_dir,
        "InstalledDependency.zip",
        "InstalledDependency",
    )

    def fake_get_mod_info(mod_name):
        if mod_name == "DownloadedDependency":
            return _mod_info("DownloadedDependency")
        return None

    def fake_download_mod(mod_info):
        mod_zip_factory(
            mods_dir,
            "DownloadedDependency-1.0.0.zip",
            "DownloadedDependency",
        )
        return mod_manager.DownloadResult(
            mod=mod_manager.Mod.from_filename("DownloadedDependency-1.0.0.zip")
        )

    monkeypatch.setattr(mod_manager, "get_mod_info", fake_get_mod_info)
    monkeypatch.setattr(mod_manager, "_download_mod", fake_download_mod)

    root = next(mod for mod in mod_manager.get_installed_mods() if mod.name == "Root")
    result = mod_manager.resolve_deps(root)

    output = capsys.readouterr().out
    assert [mod.name for mod in result.resolved] == ["DownloadedDependency"]
    assert result.issues == []
    assert "  Resolving dependency InstalledDependency (1.0.0)\n" in output
    assert "  Requirement already satisfied: InstalledDependency (1.0.0)\n" in output
    assert "  Resolving dependency DownloadedDependency (1.0.0)\n" in output
    assert "  Downloading dependency DownloadedDependency\n" in output


def test_resolve_deps_preserves_failure_kinds_and_continues_siblings(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[_dep("MissingDependency"), _dep("DownloadedDependency")],
    )

    def fake_get_mod_info(mod_name):
        if mod_name == "DownloadedDependency":
            return _mod_info(mod_name)
        return None

    def fake_download_mod(mod_info):
        mod_zip_factory(
            mods_dir,
            "DownloadedDependency.zip",
            "DownloadedDependency",
        )
        return mod_manager.DownloadResult(
            mod=mod_manager.Mod.from_filename("DownloadedDependency.zip")
        )

    monkeypatch.setattr(mod_manager, "get_mod_info", fake_get_mod_info)
    monkeypatch.setattr(mod_manager, "_download_mod", fake_download_mod)

    root = mod_manager.Mod.from_filename("Root.zip")
    assert root is not None
    result = mod_manager.resolve_deps(root)

    assert [mod.name for mod in result.resolved] == ["DownloadedDependency"]
    assert len(result.issues) == 1
    assert result.issues[0].kind == mod_manager.IssueKind.NOT_FOUND_IN_DB
    assert result.issues[0].subject == "MissingDependency"
    assert result.issues[0].dependency_chain == ("Root", "MissingDependency")


def test_resolve_deps_distinguishes_duplicate_and_database_failures(
    mods_dir: Path, mod_zip_factory, monkeypatch
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[_dep("DuplicateDependency"), _dep("DatabaseDependency")],
    )
    mod_zip_factory(mods_dir, "Duplicate-a.zip", "DuplicateDependency")
    mod_zip_factory(mods_dir, "Duplicate-b.zip", "DuplicateDependency")

    def fake_get_mod_info(mod_name):
        raise OSError(f"database unavailable for {mod_name}")

    monkeypatch.setattr(mod_manager, "get_mod_info", fake_get_mod_info)

    root = mod_manager.Mod.from_filename("Root.zip")
    assert root is not None
    result = mod_manager.resolve_deps(root)

    issues = {issue.subject: issue for issue in result.issues}
    assert issues["DuplicateDependency"].kind == (
        mod_manager.IssueKind.DUPLICATE_LOCAL_MOD
    )
    assert "Duplicate-a.zip, Duplicate-b.zip" in issues["DuplicateDependency"].detail
    assert issues["DatabaseDependency"].kind == (
        mod_manager.IssueKind.DATABASE_UNAVAILABLE
    )
    assert issues["DatabaseDependency"].retryable


def test_ensure_mod_reports_checksum_failure_status(
    mods_dir: Path, monkeypatch, capsys
):
    monkeypatch.setattr(
        mod_manager, "get_mod_info", lambda _name: _mod_info("DownloadedMod")
    )

    def fake_urlretrieve(url, filepath, reporthook=None):
        Path(filepath).write_bytes(b"unexpected complete download")

    monkeypatch.setattr(mod_manager.urllib.request, "urlretrieve", fake_urlretrieve)

    result = mod_manager.ensure_mod("DownloadedMod")

    assert result.mod is None
    assert result.status == mod_manager.EnsureModStatus.FAILED
    assert result.issues[-1].kind == mod_manager.IssueKind.CHECKSUM_FAILED
    assert capsys.readouterr().err == ""
