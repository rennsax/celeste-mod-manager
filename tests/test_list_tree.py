from pathlib import Path

import pytest

from src import config, mod_manager
from src.cli import CelesteModCLI


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


def build_three_mod_optional_cycle(mods_dir: Path, mod_zip_factory):
    mod_zip_factory(
        mods_dir,
        "MaxHelpingHand.zip",
        "MaxHelpingHand",
        optional_deps=[_dep("EeveeHelper")],
    )
    mod_zip_factory(
        mods_dir,
        "EeveeHelper.zip",
        "EeveeHelper",
        optional_deps=[_dep("StyleMaskHelper")],
    )
    mod_zip_factory(
        mods_dir,
        "StyleMaskHelper.zip",
        "StyleMaskHelper",
        optional_deps=[_dep("MaxHelpingHand")],
    )


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


@pytest.mark.parametrize("experimental", [True, False])
def test_list_tree_tolerates_optional_dependency_cycle(
    mods_dir: Path,
    mod_zip_factory,
    monkeypatch,
    capsys,
    experimental: bool,
):
    monkeypatch.setattr(config, "_ENABLE_EXPERIMENTAL_APPLY", experimental)
    build_three_mod_optional_cycle(mods_dir, mod_zip_factory)

    exit_code = CelesteModCLI().list_tree(["--optional-deps", "--maxdepth", "4"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == (
        "WARNING: optional dependency cycle detected among: "
        "EeveeHelper, MaxHelpingHand, StyleMaskHelper. Continuing because "
        "Everest permits cycles involving optional dependencies.\n"
    )
    assert "EeveeHelper (1.0.0)" in captured.out
    assert "StyleMaskHelper (1.0.0) (Optional)" in captured.out
    assert "MaxHelpingHand (1.0.0) (Optional)" in captured.out
    assert "EeveeHelper (1.0.0) (Optional) [CYCLE]" in captured.out


def test_list_tree_tolerates_mixed_required_and_optional_cycle(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "Alpha.zip", "Alpha", deps=[_dep("Beta")])
    mod_zip_factory(
        mods_dir, "Beta.zip", "Beta", optional_deps=[_dep("Alpha")]
    )

    mod_manager.analyse_mod_deps(maxdepth=3, optional=True)

    captured = capsys.readouterr()
    assert captured.err == (
        "WARNING: optional dependency cycle detected among: Alpha, Beta. "
        "Continuing because Everest permits cycles involving optional "
        "dependencies.\n"
    )
    assert captured.out == (
        "Alpha (1.0.0)\n"
        "└── Beta (1.0.0)\n"
        "    └── Alpha (1.0.0) (Optional) [CYCLE]\n"
    )


def test_list_tree_rejects_required_dependency_cycle(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "Alpha.zip", "Alpha", deps=[_dep("Beta")])
    mod_zip_factory(mods_dir, "Beta.zip", "Beta", deps=[_dep("Alpha")])

    with pytest.raises(SystemExit) as exc_info:
        mod_manager.analyse_mod_deps(maxdepth=3, optional=True)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "optional dependency cycle" not in captured.err


def test_list_tree_rejects_required_dependency_self_cycle(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(mods_dir, "SelfCycle.zip", "SelfCycle", deps=[_dep("SelfCycle")])

    with pytest.raises(SystemExit) as exc_info:
        mod_manager.analyse_mod_deps(maxdepth=2, optional=True)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "optional dependency cycle" not in captured.err


def test_list_tree_tolerates_optional_dependency_self_cycle(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(
        mods_dir,
        "SelfCycle.zip",
        "SelfCycle",
        optional_deps=[_dep("SelfCycle")],
    )

    mod_manager.analyse_mod_deps(maxdepth=2, optional=True)

    captured = capsys.readouterr()
    assert captured.err == (
        "WARNING: optional dependency cycle detected among: SelfCycle. "
        "Continuing because Everest permits cycles involving optional "
        "dependencies.\n"
    )
    assert captured.out == (
        "SelfCycle (1.0.0)\n"
        "└── SelfCycle (1.0.0) (Optional) [CYCLE]\n"
    )


def test_list_tree_prints_cycle_reached_from_root_and_disconnected_root(
    mods_dir: Path, mod_zip_factory, capsys
):
    mod_zip_factory(
        mods_dir, "Entry.zip", "Entry", deps=[_dep("AlphaHelper")]
    )
    mod_zip_factory(
        mods_dir,
        "AlphaHelper.zip",
        "AlphaHelper",
        optional_deps=[_dep("BetaHelper")],
    )
    mod_zip_factory(
        mods_dir,
        "BetaHelper.zip",
        "BetaHelper",
        optional_deps=[_dep("AlphaHelper")],
    )
    mod_zip_factory(mods_dir, "Solo.zip", "Solo")

    mod_manager.analyse_mod_deps(maxdepth=4, optional=True)

    captured = capsys.readouterr()
    assert captured.err == (
        "WARNING: optional dependency cycle detected among: "
        "AlphaHelper, BetaHelper. Continuing because Everest permits cycles "
        "involving optional dependencies.\n"
    )
    assert captured.out == (
        "Entry (1.0.0)\n"
        "└── AlphaHelper (1.0.0)\n"
        "    └── BetaHelper (1.0.0) (Optional)\n"
        "        └── AlphaHelper (1.0.0) (Optional) [CYCLE]\n"
        "\n"
        "Solo (1.0.0)\n"
    )
