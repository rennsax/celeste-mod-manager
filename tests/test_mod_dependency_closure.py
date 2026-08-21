from pathlib import Path

from src import mod_manager


def _dep(name: str, version: str = "1.0.0") -> dict[str, str]:
    return {"Name": name, "Version": version}


def _closure_names(mod_name: str, optional: bool = False) -> list[str]:
    mod = next(mod for mod in mod_manager.get_installed_mods() if mod.name == mod_name)
    return [
        closure_mod.name
        for closure_mod in mod_manager.get_mod_dependency_closure(
            mod, optional=optional
        )
    ]


def test_dependency_closure_includes_mod_and_required_recursive_dependencies(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[_dep("Dependency")],
    )
    mod_zip_factory(
        mods_dir,
        "Dependency.zip",
        "Dependency",
        deps=[_dep("TransitiveDependency")],
    )
    mod_zip_factory(mods_dir, "TransitiveDependency.zip", "TransitiveDependency")

    assert _closure_names("Root") == [
        "Root",
        "Dependency",
        "TransitiveDependency",
    ]


def test_dependency_closure_skips_optional_dependencies_by_default(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[_dep("RequiredDependency")],
        optional_deps=[_dep("OptionalDependency")],
    )
    mod_zip_factory(mods_dir, "RequiredDependency.zip", "RequiredDependency")
    mod_zip_factory(mods_dir, "OptionalDependency.zip", "OptionalDependency")

    assert _closure_names("Root") == ["Root", "RequiredDependency"]


def test_dependency_closure_includes_optional_dependencies_when_requested(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[_dep("RequiredDependency")],
        optional_deps=[_dep("OptionalDependency")],
    )
    mod_zip_factory(mods_dir, "RequiredDependency.zip", "RequiredDependency")
    mod_zip_factory(mods_dir, "OptionalDependency.zip", "OptionalDependency")

    assert _closure_names("Root", optional=True) == [
        "Root",
        "RequiredDependency",
        "OptionalDependency",
    ]


def test_dependency_closure_skips_core_missing_and_unnamed_dependencies(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        deps=[
            _dep("Everest"),
            _dep("Celeste"),
            _dep("EverestCore"),
            _dep("MissingDependency"),
            {"Version": "1.0.0"},
            {"Name": "", "Version": "1.0.0"},
            _dep("InstalledDependency"),
        ],
    )
    mod_zip_factory(mods_dir, "InstalledDependency.zip", "InstalledDependency")

    assert _closure_names("Root") == ["Root", "InstalledDependency"]


def test_dependency_closure_uses_visited_set_to_break_cycles(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(mods_dir, "Root.zip", "Root", deps=[_dep("Dependency")])
    mod_zip_factory(mods_dir, "Dependency.zip", "Dependency", deps=[_dep("Root")])

    assert _closure_names("Root") == ["Root", "Dependency"]
