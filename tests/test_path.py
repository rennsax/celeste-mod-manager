from pathlib import Path

import pytest

from src import config, path as celeste_path


def _make_celeste_dir(
    root: Path, *, marker: str = "Celeste.exe", with_mods: bool = False
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / marker).touch()
    if with_mods:
        (root / "Mods").mkdir(exist_ok=True)
    return root


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_automatic_discovery_uses_steam_root_on_windows_and_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str
):
    monkeypatch.setattr(celeste_path, "steam_find_game", lambda app_id: tmp_path)
    monkeypatch.setattr(celeste_path.platform, "system", lambda: system)

    assert celeste_path.find_celeste_dir_from_steam() == tmp_path


def test_automatic_discovery_uses_resources_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(celeste_path, "steam_find_game", lambda app_id: tmp_path)
    monkeypatch.setattr(celeste_path.platform, "system", lambda: "Darwin")

    assert celeste_path.find_celeste_dir_from_steam() == (
        tmp_path / "Celeste.app" / "Contents" / "Resources"
    )


@pytest.mark.parametrize("marker", ["Celeste.exe", "Celeste.dll"])
def test_configure_accepts_celeste_executable_or_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str
):
    celeste_dir = _make_celeste_dir(tmp_path / marker, marker=marker)
    monkeypatch.setattr(config, "CELESTE_DIR", str(celeste_dir))

    assert celeste_path.configure_celeste_dir() == celeste_dir.resolve()
    assert config.CELESTE_DIR == str(celeste_dir.resolve())


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_configure_rejects_missing_or_non_directory_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
):
    candidate = tmp_path / kind
    if kind == "file":
        candidate.touch()
    monkeypatch.setattr(config, "CELESTE_DIR", str(candidate))

    with pytest.raises(
        celeste_path.CelestePathError,
        match="configured CELESTE_DIR.*does not exist or is not a directory",
    ):
        celeste_path.configure_celeste_dir()


def test_empty_config_uses_automatic_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    celeste_dir = _make_celeste_dir(tmp_path / "automatic")
    monkeypatch.setattr(config, "CELESTE_DIR", "")
    monkeypatch.setattr(
        celeste_path, "find_celeste_dir_from_steam", lambda: celeste_dir
    )

    assert celeste_path.configure_celeste_dir() == celeste_dir.resolve()


def test_configured_path_skips_automatic_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    celeste_dir = _make_celeste_dir(tmp_path / "configured")
    monkeypatch.setattr(config, "CELESTE_DIR", str(celeste_dir))
    monkeypatch.setattr(
        celeste_path,
        "find_celeste_dir_from_steam",
        lambda: pytest.fail("configured path must skip automatic discovery"),
    )

    assert celeste_path.configure_celeste_dir() == celeste_dir.resolve()


def test_cli_override_takes_priority_over_config_and_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    configured = _make_celeste_dir(tmp_path / "configured")
    override = _make_celeste_dir(tmp_path / "override", marker="Celeste.dll")
    monkeypatch.setattr(config, "CELESTE_DIR", str(configured))
    monkeypatch.setattr(
        celeste_path,
        "find_celeste_dir_from_steam",
        lambda: pytest.fail("CLI override must skip automatic discovery"),
    )

    assert celeste_path.configure_celeste_dir(override) == override.resolve()
    assert config.CELESTE_DIR == str(override.resolve())


def test_invalid_cli_override_does_not_fall_back_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    configured = _make_celeste_dir(tmp_path / "configured")
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    monkeypatch.setattr(config, "CELESTE_DIR", str(configured))

    with pytest.raises(
        celeste_path.CelestePathError,
        match="specified Celeste directory.*neither Celeste.exe nor Celeste.dll",
    ):
        celeste_path.configure_celeste_dir(invalid)


def test_invalid_config_does_not_fall_back_to_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    invalid = tmp_path / "configured"
    invalid.mkdir()
    monkeypatch.setattr(config, "CELESTE_DIR", str(invalid))
    monkeypatch.setattr(
        celeste_path,
        "find_celeste_dir_from_steam",
        lambda: pytest.fail("invalid non-empty config must not fall back"),
    )

    with pytest.raises(
        celeste_path.CelestePathError,
        match="configured CELESTE_DIR.*neither Celeste.exe nor Celeste.dll",
    ):
        celeste_path.configure_celeste_dir()


def test_missing_and_stale_automatic_discovery_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "CELESTE_DIR", "")
    monkeypatch.setattr(celeste_path, "find_celeste_dir_from_steam", lambda: None)
    with pytest.raises(celeste_path.CelestePathError, match="Could not find Celeste"):
        celeste_path.configure_celeste_dir()

    stale = tmp_path / "stale"
    stale.mkdir()
    monkeypatch.setattr(celeste_path, "find_celeste_dir_from_steam", lambda: stale)
    with pytest.raises(
        celeste_path.CelestePathError,
        match="automatically detected Celeste directory.*not a valid Celeste",
    ):
        celeste_path.configure_celeste_dir()


def test_relative_config_is_normalized_to_an_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    celeste_dir = _make_celeste_dir(tmp_path / "game")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "CELESTE_DIR", "game")

    assert celeste_path.configure_celeste_dir() == celeste_dir.resolve()
    assert config.CELESTE_DIR == str(celeste_dir.resolve())


def test_macos_app_bundle_root_is_not_implicitly_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_dir = tmp_path / "Celeste.app"
    resources = _make_celeste_dir(app_dir / "Contents" / "Resources")
    monkeypatch.setattr(config, "CELESTE_DIR", "")

    with pytest.raises(celeste_path.CelestePathError, match="not a valid Celeste"):
        celeste_path.configure_celeste_dir(app_dir)

    assert celeste_path.configure_celeste_dir(resources) == resources.resolve()


def test_mods_directory_validation_distinguishes_missing_empty_and_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    celeste_dir = _make_celeste_dir(tmp_path / "game")
    monkeypatch.setattr(config, "CELESTE_DIR", str(celeste_dir))
    mods_dir = celeste_dir / "Mods"

    with pytest.raises(
        celeste_path.CelestePathError,
        match="Everest may not be installed or may be damaged",
    ):
        celeste_path.validate_mods_dir()

    mods_dir.touch()
    with pytest.raises(celeste_path.CelestePathError, match="expected a directory"):
        celeste_path.validate_mods_dir()

    mods_dir.unlink()
    mods_dir.mkdir()
    assert celeste_path.validate_mods_dir() == mods_dir


def test_unreadable_mods_directory_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    celeste_dir = _make_celeste_dir(tmp_path / "game", with_mods=True)
    monkeypatch.setattr(config, "CELESTE_DIR", str(celeste_dir))
    monkeypatch.setattr(
        celeste_path.os,
        "scandir",
        lambda path: (_ for _ in ()).throw(PermissionError("permission denied")),
    )

    with pytest.raises(celeste_path.CelestePathError, match="permission denied"):
        celeste_path.validate_mods_dir()


def test_database_path_may_be_missing_but_must_not_be_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    celeste_dir = _make_celeste_dir(tmp_path / "game", with_mods=True)
    monkeypatch.setattr(config, "CELESTE_DIR", str(celeste_dir))
    db_path = celeste_path.get_mod_db_path()

    assert celeste_path.validate_mod_db_path() == db_path

    db_path.mkdir()
    with pytest.raises(celeste_path.CelestePathError, match="expected a file"):
        celeste_path.validate_mod_db_path()
