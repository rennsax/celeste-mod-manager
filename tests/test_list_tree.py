from pathlib import Path

from src import config, mod_manager


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


def use_legacy_list_tree(monkeypatch):
    monkeypatch.setattr(config, "_ENABLE_EXPERIMENTAL_APPLY", False)


def test_list_tree_prints_required_dependency_tree(
    mods_dir: Path, mod_zip_factory, installed_mods_writer, monkeypatch, capsys
):
    use_legacy_list_tree(monkeypatch)
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
    mods_dir: Path, mod_zip_factory, installed_mods_writer, monkeypatch, capsys
):
    use_legacy_list_tree(monkeypatch)
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


def test_list_tree_marks_disabled_mods(
    mods_dir: Path, mod_zip_factory, installed_mods_writer, monkeypatch, capsys
):
    use_legacy_list_tree(monkeypatch)
    build_complex_mod_library(mods_dir, mod_zip_factory, installed_mods_writer)
    (mods_dir / "blacklist.txt").write_text(
        "AdventurePack.zip\nCoreLib.zip\n", encoding="utf-8"
    )

    mod_manager.analyse_mod_deps(maxdepth=2)

    output = capsys.readouterr().out
    assert_contains_all(
        output,
        [
            "AdventurePack (1.0.0) \x1b[91m[DISABLED]\x1b[0m\n",
            "├── CoreLib (1.0.0) \x1b[91m[DISABLED]\x1b[0m\n",
        ],
    )
    assert "MapPack (1.0.0) \x1b[91m[DISABLED]\x1b[0m" not in output


def test_list_tree_enabled_only_filters_disabled_roots_but_keeps_dependencies(
    mods_dir: Path, mod_zip_factory, installed_mods_writer, monkeypatch, capsys
):
    use_legacy_list_tree(monkeypatch)
    build_complex_mod_library(mods_dir, mod_zip_factory, installed_mods_writer)
    (mods_dir / "blacklist.txt").write_text(
        "AdventurePack.zip\nCoreLib.zip\n", encoding="utf-8"
    )

    mod_manager.analyse_mod_deps(maxdepth=2, enabled_only=True)

    output = capsys.readouterr().out
    assert_contains_all(
        output,
        [
            "MapPack (1.0.0)",
            "├── CoreLib (1.0.0) \x1b[91m[DISABLED]\x1b[0m\n",
        ],
    )
    assert "AdventurePack" not in output


def test_list_tree_prints_no_mods_when_empty(mods_dir: Path, capsys):
    mod_manager.analyse_mod_deps(maxdepth=2)

    assert capsys.readouterr().out == "No mods installed.\n"


def test_experimental_list_tree_only_prints_enabled_mod_tree(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(
        mods_dir,
        "AdventurePack.zip",
        "AdventurePack",
        deps=[_dep("CoreLib"), _dep("MapPack"), _dep("MissingDependency")],
        optional_deps=[_dep("OptionalSkin")],
    )
    mod_zip_factory(mods_dir, "CoreLib.zip", "CoreLib")
    mod_zip_factory(mods_dir, "MapPack.zip", "MapPack", deps=[_dep("CoreLib")])
    mod_zip_factory(mods_dir, "OptionalSkin.zip", "OptionalSkin")
    mod_zip_factory(mods_dir, "DisabledDependency.zip", "DisabledDependency")
    mod_zip_factory(
        mods_dir,
        "DisabledRoot.zip",
        "DisabledRoot",
        deps=[_dep("DisabledDependency")],
    )
    (mods_dir / "blacklist.txt").write_text(
        "CoreLib.zip\nDisabledRoot.zip\nDisabledDependency.zip\n",
        encoding="utf-8",
    )

    mod_manager.analyse_mod_deps(maxdepth=3)

    output = capsys.readouterr().out
    assert "AdventurePack (1.0.0)\n└── MapPack (1.0.0)\n" in output
    assert "CoreLib" not in output
    assert "DisabledRoot" not in output
    assert "DisabledDependency" not in output
    assert "MissingDependency" not in output
    assert "[DISABLED]" not in output
    assert "[ORPHAN]" not in output
    assert "Orphan root mod(s)" not in output
    assert "optionally depended by" not in output


def test_experimental_list_tree_optional_deps_are_controlled_by_option(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(
        mods_dir,
        "AdventurePack.zip",
        "AdventurePack",
        optional_deps=[_dep("OptionalSkin")],
    )
    mod_zip_factory(mods_dir, "OptionalSkin.zip", "OptionalSkin")

    mod_manager.analyse_mod_deps(maxdepth=2)
    default_output = capsys.readouterr().out

    mod_manager.analyse_mod_deps(maxdepth=2, optional=True)
    optional_output = capsys.readouterr().out

    assert "OptionalSkin" in default_output
    assert "optionally depended by" not in default_output
    assert (
        "AdventurePack (1.0.0)\n└── OptionalSkin (1.0.0) (Optional)\n"
        in optional_output
    )


def test_experimental_list_tree_prints_no_mods_when_all_mods_are_disabled(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "Root.zip", "Root")
    (mods_dir / "blacklist.txt").write_text("Root.zip\n", encoding="utf-8")

    mod_manager.analyse_mod_deps(maxdepth=2)

    assert capsys.readouterr().out == "No mods installed.\n"
