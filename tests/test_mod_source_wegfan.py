import json

import pytest

from src.mod_source import CatalogFormatError, ModSourceMismatchError, ModSourceName
from src.mod_source.base import ModInfo
from src.mod_source.wegfan import WEGFAN_API_URL, WegfanModSource


class FakeResponse:
    def __init__(self, document):
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.document).encode("utf-8")


def _wegfan_entry():
    return {
        "name": "ExampleMod",
        "version": "2.0.0",
        "xxHash": ["1111111111111111", "2222222222222222"],
        "submissionFile": {
            "id": "wegfan-file",
            "url": "https://celeste.weg.fan/api/v2/mod/download/wegfan-file",
            "size": 4096,
            "downloads": 123,
            "gameBananaId": 456,
            "submission": {"pageUrl": "https://gamebanana.com/mods/789"},
        },
    }


def test_fetch_catalog_normalizes_wegfan_private_structure(monkeypatch):
    backend = WegfanModSource()
    monkeypatch.setattr(
        "src.mod_source.wegfan.urllib.request.urlopen",
        lambda url: FakeResponse({"data": [_wegfan_entry()]}),
    )

    assert backend.fetch_catalog() == [
        ModInfo(
            source=ModSourceName.WEGFAN,
            name="ExampleMod",
            version="2.0.0",
            xxhashes=("1111111111111111", "2222222222222222"),
            download_url=("https://celeste.weg.fan/api/v2/mod/download/wegfan-file"),
            size=4096,
            page_url="https://gamebanana.com/mods/789",
            downloads=123,
            remote_file_id="wegfan-file",
        )
    ]


def test_wegfan_download_request_rejects_wrong_source_and_url():
    backend = WegfanModSource()
    valid = backend._normalize_entry(_wegfan_entry())
    assert backend.build_download_request(valid).full_url.startswith(WEGFAN_API_URL)

    with pytest.raises(ModSourceMismatchError):
        backend.build_download_request(
            ModInfo(
                **{
                    **valid.__dict__,
                    "source": ModSourceName.GAMEBANANA,
                }
            )
        )
    with pytest.raises(CatalogFormatError):
        backend.build_download_request(
            ModInfo(**{**valid.__dict__, "download_url": "https://example.com/a"})
        )


def test_wegfan_cache_does_not_touch_legacy_database(mods_dir, monkeypatch):
    legacy_path = mods_dir / "celeste_mod_db.json"
    legacy_contents = "legacy database remains untouched"
    legacy_path.write_text(legacy_contents, encoding="utf-8")
    monkeypatch.setattr(
        "src.mod_source.wegfan.urllib.request.urlopen",
        lambda url: FakeResponse({"data": [_wegfan_entry()]}),
    )

    WegfanModSource().get_mod_db(force_update=True)

    assert legacy_path.read_text(encoding="utf-8") == legacy_contents
    assert (mods_dir / "celeste_mod_db.wegfan.json").is_file()
