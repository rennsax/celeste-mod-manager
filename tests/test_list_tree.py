from pathlib import Path

from src import mod_manager


def _dep(name: str, version: str = "1.0.0") -> dict[str, str]:
    return {"Name": name, "Version": version}


def build_complex_mod_library(mods_dir: Path, mod_zip_factory, installed_mods_writer):
    mod_zip_factory(
        mods_dir,
        "AdventurePack.zip",
        "AdventurePack",
        deps=[_dep("CoreLib"), _dep("MapPack"), _dep("MissingDependency")],
        optional_deps=[_dep("OptionalSkin")],
    )
    mod_zip_factory(
        mods_dir,
        "MapPack.zip",
        "MapPack",
        deps=[_dep("CoreLib"), _dep("SharedAssets")],
    )
    mod_zip_factory(
        mods_dir,
        "CoreLib.zip",
        "CoreLib",
        deps=[_dep("SharedAssets")],
    )
    mod_zip_factory(mods_dir, "SharedAssets.zip", "SharedAssets")
    mod_zip_factory(mods_dir, "OptionalSkin.zip", "OptionalSkin")
    mod_zip_factory(mods_dir, "SoloChallenge.zip", "SoloChallenge")
    installed_mods_writer(
        mods_dir,
        [
            {
                "name": "AdventurePack",
                "version": "1.0.0",
                "filename": "AdventurePack.zip",
            }
        ],
    )


def assert_contains_all(output: str, fragments: list[str]):
    for fragment in fragments:
        assert fragment in output


def test_list_tree_prints_required_dependency_tree(
    mods_dir: Path, mod_zip_factory, installed_mods_writer, capsys
):
    build_complex_mod_library(mods_dir, mod_zip_factory, installed_mods_writer)

    mod_manager.analyse_mod_deps(maxdepth=4)

    output = capsys.readouterr().out
    assert_contains_all(
        output,
        [
            "AdventurePack (1.0.0)\n"
            "├── CoreLib (1.0.0)\n"
            "│   └── SharedAssets (1.0.0)\n",
            "├── MapPack (1.0.0)\n"
            "│   ├── CoreLib (1.0.0)\n"
            "│   │   └── SharedAssets (1.0.0)\n"
            "│   └── SharedAssets (1.0.0)\n",
            "└── \x1b[91mMissingDependency (Missing)\x1b[0m\n",
            "OptionalSkin (1.0.0) (optionally depended by AdventurePack)",
            "SoloChallenge (1.0.0) \x1b[1;33m[ORPHAN]\x1b[0m",
        ],
    )
    assert "OptionalSkin (1.0.0) \x1b[1;33m[ORPHAN]\x1b[0m" not in output


def test_list_tree_includes_optional_dependencies_when_requested(
    mods_dir: Path, mod_zip_factory, installed_mods_writer, capsys
):
    build_complex_mod_library(mods_dir, mod_zip_factory, installed_mods_writer)

    mod_manager.analyse_mod_deps(maxdepth=2, optional=True)

    output = capsys.readouterr().out
    assert_contains_all(
        output,
        [
            "AdventurePack (1.0.0)\n"
            "├── CoreLib (1.0.0)\n"
            "├── MapPack (1.0.0)\n"
            "├── \x1b[91mMissingDependency (Missing)\x1b[0m\n"
            "└── OptionalSkin (1.0.0) (Optional)\n",
            "SoloChallenge (1.0.0) \x1b[1;33m[ORPHAN]\x1b[0m",
        ],
    )
    assert "OptionalSkin (1.0.0) \x1b[1;33m[ORPHAN]\x1b[0m" not in output
    assert "OptionalSkin (1.0.0) (optionally depended by AdventurePack)" not in output


def test_list_tree_prints_no_mods_when_empty(mods_dir: Path, capsys):
    mod_manager.analyse_mod_deps(maxdepth=2)

    assert capsys.readouterr().out == "No mods installed.\n"
