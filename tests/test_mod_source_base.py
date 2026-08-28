import json
import time
import urllib.request
from pathlib import Path

import pytest

from src import config, mod_source
from src.mod_source.base import (
    CatalogFormatError,
    ModInfo,
    ModSourceBackend,
    ModSourceMismatchError,
    ModSourceName,
)


def _mod_info(
    name: str = "Example",
    *,
    source: ModSourceName = ModSourceName.WEGFAN,
) -> ModInfo:
    return ModInfo(
        source=source,
        name=name,
        version="1.2.3",
        xxhashes=("1111111111111111", "2222222222222222"),
        download_url=f"https://example.invalid/{name}.zip",
        size=1234,
        page_url="https://example.invalid/mods/1",
        downloads=42,
        remote_file_id="99",
    )


class FakeBackend(ModSourceBackend):
    name = ModSourceName.WEGFAN
    cache_filename = "celeste_mod_db.fake.json"

    def __init__(self, catalog: list[ModInfo] | None = None):
        self.catalog = catalog if catalog is not None else [_mod_info()]
        self.fetch_count = 0
        self.fetch_error: Exception | None = None

    def fetch_catalog(self) -> list[ModInfo]:
        self.fetch_count += 1
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.catalog

    def build_download_request(self, mod_info: ModInfo) -> urllib.request.Request:
        self._ensure_matching_source(mod_info)
        return urllib.request.Request(mod_info.download_url)


def test_incomplete_backend_cannot_be_instantiated():
    class IncompleteBackend(ModSourceBackend):
        name = ModSourceName.WEGFAN
        cache_filename = "unused.json"

    with pytest.raises(TypeError):
        IncompleteBackend()


def test_mod_info_cache_round_trip_is_lossless_and_snake_case():
    mod_info = _mod_info()

    serialized = mod_info.to_cache_dict()

    assert ModInfo.from_cache_dict(serialized) == mod_info
    assert set(serialized) == {
        "source",
        "name",
        "version",
        "xxhashes",
        "download_url",
        "size",
        "page_url",
        "downloads",
        "remote_file_id",
    }


def test_backend_publishes_normalized_cache_and_reuses_it(mods_dir: Path, capsys):
    backend = FakeBackend()

    assert backend.get_mod_db(force_update=True) == backend.catalog
    assert capsys.readouterr().out == "Updating the local mod database...\n"
    assert backend.get_mod_db() == backend.catalog
    assert capsys.readouterr().out == ""
    assert backend.fetch_count == 1

    document = json.loads(
        (mods_dir / backend.cache_filename).read_text(encoding="utf-8")
    )
    assert document["schema_version"] == 1
    assert document["source"] == "wegfan"
    assert document["data"] == [backend.catalog[0].to_cache_dict()]


def test_stale_refresh_failure_uses_only_same_source_cache(mods_dir: Path, monkeypatch):
    backend = FakeBackend()
    backend.get_mod_db(force_update=True)
    cache_path = mods_dir / backend.cache_filename
    document = json.loads(cache_path.read_text(encoding="utf-8"))
    document["last_update_time"] = time.time() - 10 * 86400
    cache_path.write_text(json.dumps(document), encoding="utf-8")
    backend.fetch_error = OSError("offline")
    monkeypatch.setattr(config, "DB_UPDATE_PERIOD_DAYS", 7)

    assert backend.get_mod_db() == backend.catalog
    with pytest.raises(OSError, match="offline"):
        backend.get_mod_db(force_update=True)


def test_backend_rejects_catalog_entries_from_another_source(mods_dir: Path):
    backend = FakeBackend([_mod_info(source=ModSourceName.GAMEBANANA)])

    with pytest.raises(ModSourceMismatchError):
        backend.get_mod_db(force_update=True)
    assert not (mods_dir / backend.cache_filename).exists()


def test_corrupt_cache_is_not_used_when_refresh_also_fails(mods_dir: Path):
    backend = FakeBackend()
    (mods_dir / backend.cache_filename).write_text("not json", encoding="utf-8")
    backend.fetch_error = OSError("offline")

    with pytest.raises(OSError, match="offline"):
        backend.get_mod_db()


def test_search_order_matches_direct_then_fuzzy_behavior(monkeypatch):
    backend = FakeBackend()
    mods = [
        _mod_info("Another-Strawberry"),
        _mod_info("StrawberryJam2021"),
        _mod_info("Strawberry"),
    ]
    monkeypatch.setattr(backend, "get_mod_db", lambda: mods)

    assert [mod.name for mod in backend.search_mod_by_name("strawberry")] == [
        "Strawberry",
        "StrawberryJam2021",
        "Another-Strawberry",
    ]
    assert [mod.name for mod in backend.search_mod_by_name("Strawbery")] == [
        "Another-Strawberry",
        "Strawberry",
        "StrawberryJam2021",
    ]


def test_invalid_cached_source_is_rejected(mods_dir: Path):
    backend = FakeBackend()
    cache_path = mods_dir / backend.cache_filename
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "gamebanana",
                "last_update_time": time.time(),
                "data": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ModSourceMismatchError):
        backend.get_cached_mod_db()


def test_invalid_cache_entry_reports_catalog_format_error(mods_dir: Path):
    backend = FakeBackend()
    cache_path = mods_dir / backend.cache_filename
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "wegfan",
                "last_update_time": time.time(),
                "data": [{"name": "missing fields"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CatalogFormatError):
        backend.get_cached_mod_db()


def test_facade_resolves_config_after_import_and_rejects_cross_source_downloads(
    mods_dir: Path, monkeypatch
):
    class FakeGameBananaBackend(FakeBackend):
        name = ModSourceName.GAMEBANANA
        cache_filename = "celeste_mod_db.fake-gamebanana.json"

    gamebanana_info = _mod_info(source=ModSourceName.GAMEBANANA)
    fake_gamebanana = FakeGameBananaBackend([gamebanana_info])
    monkeypatch.setitem(mod_source._BACKENDS, ModSourceName.GAMEBANANA, fake_gamebanana)

    monkeypatch.setattr(config, "MOD_SOURCE", "gamebanana")
    assert mod_source.get_current_source() == ModSourceName.GAMEBANANA
    assert mod_source.get_mod_info("Example") == gamebanana_info
    assert mod_source.search_mod_by_name("Example") == [gamebanana_info]
    assert mod_source.get_download_request(gamebanana_info).full_url == (
        gamebanana_info.download_url
    )

    monkeypatch.setattr(config, "MOD_SOURCE", "wegfan")
    with pytest.raises(ModSourceMismatchError):
        mod_source.get_download_request(gamebanana_info)


def test_configure_override_has_priority_and_validates_source(monkeypatch):
    monkeypatch.setattr(config, "MOD_SOURCE", "gamebanana")
    assert mod_source.configure("wegfan") == ModSourceName.WEGFAN
    assert config.MOD_SOURCE == "wegfan"

    with pytest.raises(mod_source.InvalidModSourceError, match="invalid mod source"):
        mod_source.configure("unknown")


def test_facade_reports_source_specific_cache_filenames(monkeypatch):
    monkeypatch.setattr(config, "MOD_SOURCE", "wegfan")
    assert mod_source.get_cache_filename() == "celeste_mod_db.wegfan.json"

    monkeypatch.setattr(config, "MOD_SOURCE", "gamebanana")
    assert mod_source.get_cache_filename() == "celeste_mod_db.gamebanana.json"


def test_missing_mod_is_not_looked_up_in_another_backend(mods_dir, monkeypatch):
    wegfan = FakeBackend([_mod_info("OnlyOnWegfan")])

    class EmptyGameBananaBackend(FakeBackend):
        name = ModSourceName.GAMEBANANA
        cache_filename = "celeste_mod_db.empty-gamebanana.json"

    gamebanana = EmptyGameBananaBackend([])
    monkeypatch.setitem(mod_source._BACKENDS, ModSourceName.WEGFAN, wegfan)
    monkeypatch.setitem(mod_source._BACKENDS, ModSourceName.GAMEBANANA, gamebanana)
    monkeypatch.setattr(config, "MOD_SOURCE", "gamebanana")

    assert mod_source.get_mod_info("OnlyOnWegfan") is None
    assert gamebanana.fetch_count == 1
    assert wegfan.fetch_count == 0
