from pathlib import Path

from src import config, mod_manager


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


def _installed_mod(mod_name: str):
    return next(mod for mod in mod_manager.get_installed_mods() if mod.name == mod_name)


def _exclusive_closure_names(mod_name: str) -> list[str]:
    return [
        mod.name
        for mod in mod_manager.get_mods_exclusively_depending_on_closure(
            _installed_mod(mod_name)
        )
    ]


def test_get_root_mods_returns_empty_when_root_tracking_disabled_without_parsing(
    mods_dir: Path, monkeypatch
):
    monkeypatch.setattr(config, "_ENABLE_ROOT_INSTALL_TRACK", False)
    (mods_dir / "installed_mods.yml").write_text("not: [valid\n", encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("installed_mods.yml should not be parsed")

    monkeypatch.setattr(mod_manager.yaml, "safe_load", fail_if_called)

    assert mod_manager.get_root_mods() == []


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


def test_exclusive_closure_includes_dependencies_used_only_by_target(
    mods_dir: Path, mod_zip_factory, installed_mods_writer
):
    mod_zip_factory(mods_dir, "Root.zip", "Root", deps=[_dep("Beta"), _dep("Alpha")])
    mod_zip_factory(mods_dir, "Alpha.zip", "Alpha")
    mod_zip_factory(mods_dir, "Beta.zip", "Beta")
    installed_mods_writer(
        mods_dir,
        [{"name": "Root", "version": "1.0.0", "filename": "Root.zip"}],
    )

    assert _exclusive_closure_names("Root") == ["Root", "Alpha", "Beta"]


def test_exclusive_closure_excludes_shared_dependencies(
    mods_dir: Path, mod_zip_factory, installed_mods_writer
):
    mod_zip_factory(mods_dir, "Root.zip", "Root", deps=[_dep("SharedDependency")])
    mod_zip_factory(mods_dir, "OtherRoot.zip", "OtherRoot", deps=[_dep("SharedDependency")])
    mod_zip_factory(mods_dir, "SharedDependency.zip", "SharedDependency")
    installed_mods_writer(
        mods_dir,
        [
            {"name": "Root", "version": "1.0.0", "filename": "Root.zip"},
            {"name": "OtherRoot", "version": "1.0.0", "filename": "OtherRoot.zip"},
        ],
    )

    assert _exclusive_closure_names("Root") == ["Root"]


def test_exclusive_closure_excludes_root_dependencies(
    mods_dir: Path, mod_zip_factory, installed_mods_writer
):
    mod_zip_factory(mods_dir, "Root.zip", "Root", deps=[_dep("DependencyRoot")])
    mod_zip_factory(mods_dir, "DependencyRoot.zip", "DependencyRoot")
    installed_mods_writer(
        mods_dir,
        [
            {"name": "Root", "version": "1.0.0", "filename": "Root.zip"},
            {
                "name": "DependencyRoot",
                "version": "1.0.0",
                "filename": "DependencyRoot.zip",
            },
        ],
    )

    assert _exclusive_closure_names("Root") == ["Root"]


def test_exclusive_closure_prunes_dependencies_of_excluded_shared_dependency(
    mods_dir: Path, mod_zip_factory, installed_mods_writer
):
    mod_zip_factory(mods_dir, "Root.zip", "Root", deps=[_dep("SharedParent")])
    mod_zip_factory(mods_dir, "OtherRoot.zip", "OtherRoot", deps=[_dep("SharedParent")])
    mod_zip_factory(mods_dir, "SharedParent.zip", "SharedParent", deps=[_dep("Leaf")])
    mod_zip_factory(mods_dir, "Leaf.zip", "Leaf")
    installed_mods_writer(
        mods_dir,
        [
            {"name": "Root", "version": "1.0.0", "filename": "Root.zip"},
            {"name": "OtherRoot", "version": "1.0.0", "filename": "OtherRoot.zip"},
        ],
    )

    assert _exclusive_closure_names("Root") == ["Root"]


def test_exclusive_closure_uses_optional_dependencies(
    mods_dir: Path, mod_zip_factory, installed_mods_writer
):
    mod_zip_factory(
        mods_dir,
        "Root.zip",
        "Root",
        optional_deps=[_dep("OptionalDependency")],
    )
    mod_zip_factory(mods_dir, "OptionalDependency.zip", "OptionalDependency")
    installed_mods_writer(
        mods_dir,
        [{"name": "Root", "version": "1.0.0", "filename": "Root.zip"}],
    )

    assert _exclusive_closure_names("Root") == ["Root", "OptionalDependency"]
