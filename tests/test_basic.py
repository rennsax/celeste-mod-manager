import json
import time
from pathlib import Path

from src import config, mod_db, mod_manager
from src.path import set_mod_paths


def test_mods_dir_fixture_monkeypatches_config(mods_dir: Path):
    assert config.MODS_DIR == str(mods_dir)


def test_set_mod_paths_stores_mod_db_in_mods_dir(tmp_path: Path):
    set_mod_paths(tmp_path)

    assert config.MODS_DIR == str(tmp_path / "Mods")
    assert config.MOD_DB_PATH == str(tmp_path / "Mods" / "celeste_mod_db.json")


def test_dummy_mod_zip_is_loaded_from_monkeypatched_mods_dir(
    mods_dir: Path, mod_zip_factory
):
    mod_zip_factory(mods_dir, "random-local-name.zip", "DummyMod", "1.2.3")

    mods = mod_manager.get_installed_mods()

    assert len(mods) == 1
    assert mods[0].name == "DummyMod"
    assert mods[0].version == "1.2.3"
    assert mods[0].get_filename() == "random-local-name.zip"


def test_get_mod_db_notifies_when_refreshing(tmp_path: Path, monkeypatch, capsys):
    db_path = tmp_path / "celeste_mod_db.json"
    monkeypatch.setattr(config, "MOD_DB_PATH", str(db_path))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({"data": []}).encode("utf-8")

    monkeypatch.setattr(mod_db.urllib.request, "urlopen", lambda _url: FakeResponse())

    assert (
        mod_db.get_mod_db("https://example.invalid/mod/list", force_update=True) == []
    )
    assert capsys.readouterr().out == "Updating the local mod database...\n"


def test_get_mod_db_does_not_notify_when_using_fresh_cache(
    tmp_path: Path, monkeypatch, capsys
):
    db_path = tmp_path / "celeste_mod_db.json"
    db_path.write_text(
        json.dumps({"lastUpdateTime": time.time(), "data": []}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "MOD_DB_PATH", str(db_path))

    def fail_if_refreshed(_url):
        raise AssertionError("a fresh cache must not trigger a refresh")

    monkeypatch.setattr(mod_db.urllib.request, "urlopen", fail_if_refreshed)

    assert mod_db.get_mod_db("https://example.invalid/mod/list") == []
    assert capsys.readouterr().out == ""
