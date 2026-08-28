import pytest

from src.mod_source import CatalogFormatError, ModSourceMismatchError, ModSourceName
from src.mod_source.base import ModInfo
from src.mod_source.gamebanana import (
    EVEREST_UPDATE_URL,
    MOD_SEARCH_DATABASE_URL,
    PROJECT_USER_AGENT,
    GameBananaModSource,
)


def _update_catalog(url: str = "https://gamebanana.com/mmdl/123"):
    return {
        "ExampleMod": {
            "Version": "2.0.0",
            "GameBananaFileId": 123,
            "URL": url,
            "xxHash": ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"],
            "Size": 2048,
        }
    }


def _search_catalog():
    return [
        {
            "Name": "Submission title",
            "PageURL": "https://gamebanana.com/mods/456",
            "Files": [
                {
                    "URL": "https://gamebanana.com/mmdl/123",
                    "Downloads": 99,
                }
            ],
        }
    ]


def test_fetch_catalog_joins_search_metadata_by_file_id(monkeypatch):
    backend = GameBananaModSource()
    documents = {
        EVEREST_UPDATE_URL: _update_catalog(),
        MOD_SEARCH_DATABASE_URL: _search_catalog(),
    }
    monkeypatch.setattr(backend, "_fetch_yaml", lambda url: documents[url])

    assert backend.fetch_catalog() == [
        ModInfo(
            source=ModSourceName.GAMEBANANA,
            name="ExampleMod",
            version="2.0.0",
            xxhashes=("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"),
            download_url="https://gamebanana.com/mmdl/123",
            size=2048,
            page_url="https://gamebanana.com/mods/456",
            downloads=99,
            remote_file_id="123",
        )
    ]


def test_missing_search_metadata_does_not_remove_installable_entry(monkeypatch):
    backend = GameBananaModSource()
    documents = {
        EVEREST_UPDATE_URL: _update_catalog(),
        MOD_SEARCH_DATABASE_URL: [],
    }
    monkeypatch.setattr(backend, "_fetch_yaml", lambda url: documents[url])

    result = backend.fetch_catalog()

    assert len(result) == 1
    assert result[0].page_url is None
    assert result[0].downloads is None


def test_missing_optional_size_does_not_remove_installable_entry(monkeypatch):
    backend = GameBananaModSource()
    update_catalog = _update_catalog()
    del update_catalog["ExampleMod"]["Size"]
    documents = {
        EVEREST_UPDATE_URL: update_catalog,
        MOD_SEARCH_DATABASE_URL: _search_catalog(),
    }
    monkeypatch.setattr(backend, "_fetch_yaml", lambda url: documents[url])

    result = backend.fetch_catalog()

    assert len(result) == 1
    assert result[0].size is None


@pytest.mark.parametrize(
    "url",
    [
        "http://gamebanana.com/mmdl/123",
        "https://example.com/mmdl/123",
        "https://gamebanana.com/mods/123",
        "https://gamebanana.com/mmdl/123?mirror=1",
    ],
)
def test_non_official_gamebanana_download_urls_are_rejected(monkeypatch, url):
    backend = GameBananaModSource()
    documents = {
        EVEREST_UPDATE_URL: _update_catalog(url),
        MOD_SEARCH_DATABASE_URL: _search_catalog(),
    }
    monkeypatch.setattr(
        backend, "_fetch_yaml", lambda catalog_url: documents[catalog_url]
    )

    with pytest.raises(CatalogFormatError, match="no valid entries"):
        backend.fetch_catalog()


def test_download_request_has_required_headers_and_enforces_source():
    backend = GameBananaModSource()
    mod_info = backend._normalize_entry(
        "ExampleMod",
        _update_catalog()["ExampleMod"],
        {"123": (None, None)},
    )

    request = backend.build_download_request(mod_info)

    assert request.get_header("User-agent") == PROJECT_USER_AGENT
    assert request.get_header("Accept") == "application/octet-stream"
    with pytest.raises(ModSourceMismatchError):
        backend.build_download_request(
            ModInfo(**{**mod_info.__dict__, "source": ModSourceName.WEGFAN})
        )


def test_second_catalog_failure_does_not_replace_existing_cache(mods_dir, monkeypatch):
    backend = GameBananaModSource()
    existing = backend._normalize_entry(
        "Existing",
        {
            "Version": "1.0.0",
            "GameBananaFileId": 321,
            "URL": "https://gamebanana.com/mmdl/321",
            "xxHash": ["1111111111111111"],
            "Size": 100,
        },
        {},
    )
    backend._publish_cache([existing])

    def fail_on_search_catalog(url):
        if url == EVEREST_UPDATE_URL:
            return _update_catalog()
        raise OSError("search catalog unavailable")

    monkeypatch.setattr(backend, "_fetch_yaml", fail_on_search_catalog)

    with pytest.raises(OSError, match="search catalog unavailable"):
        backend.get_mod_db(force_update=True)
    assert backend.get_cached_mod_db() == [existing]
