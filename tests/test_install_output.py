from pathlib import Path
from types import SimpleNamespace

from src import mod_manager


def _dep(name: str, version: str = "1.0.0") -> dict[str, str]:
    return {"Name": name, "Version": version}


def _mod_info(name: str, version: str = "1.0.0", size: int = 100):
    return SimpleNamespace(
        name=name,
        version=version,
        submissionFile=SimpleNamespace(
            url=f"https://example.invalid/{name}.zip",
            size=size,
        ),
    )


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

    mod = mod_manager._download_mod(_mod_info("DownloadedMod"))

    output = capsys.readouterr().out
    assert mod is not None
    assert mod.name == "DownloadedMod"
    assert "Collecting DownloadedMod\n" in output
    assert "  Downloading DownloadedMod-1.0.0.zip (100 B)\n" in output
    assert "100% 100 B/100 B" in output
    assert "  Saved DownloadedMod-1.0.0.zip\n" in output


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
        return mod_manager.Mod.from_filename("DownloadedDependency-1.0.0.zip")

    monkeypatch.setattr(mod_manager, "get_mod_info", fake_get_mod_info)
    monkeypatch.setattr(mod_manager, "_download_mod", fake_download_mod)

    root = next(mod for mod in mod_manager.get_installed_mods() if mod.name == "Root")
    resolved_deps, failed_deps = mod_manager.resolve_deps(root)

    output = capsys.readouterr().out
    assert [mod.name for mod in resolved_deps] == ["DownloadedDependency"]
    assert failed_deps == []
    assert "  Resolving dependency InstalledDependency (1.0.0)\n" in output
    assert "  Requirement already satisfied: InstalledDependency (1.0.0)\n" in output
    assert "  Resolving dependency DownloadedDependency (1.0.0)\n" in output
    assert "  Downloading dependency DownloadedDependency\n" in output
