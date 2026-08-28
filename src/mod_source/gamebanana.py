import re
import urllib.request
from collections.abc import Mapping
from importlib import metadata

import yaml
from loguru import logger

from .base import CatalogFormatError, ModInfo, ModSourceBackend, ModSourceName

EVEREST_UPDATE_URL = "https://maddie480.ovh/celeste/everest_update.yaml"
MOD_SEARCH_DATABASE_URL = "https://maddie480.ovh/celeste/mod_search_database.yaml"

try:
    _PACKAGE_VERSION = metadata.version("celeste-mod-manager")
except metadata.PackageNotFoundError:
    _PACKAGE_VERSION = "unknown"

PROJECT_USER_AGENT = (
    f"celeste-mod-manager/{_PACKAGE_VERSION} "
    "(+https://github.com/rennsax/celeste-mod-manager)"
)
_GAMEBANANA_DOWNLOAD_RE = re.compile(
    r"^https://gamebanana\.com/(?:mmdl|dl)/(?P<file_id>[0-9]+)$"
)


class GameBananaModSource(ModSourceBackend):
    name = ModSourceName.GAMEBANANA
    cache_filename = "celeste_mod_db.gamebanana.json"

    @staticmethod
    def _fetch_yaml(url: str) -> object:
        request = urllib.request.Request(
            url, headers={"User-Agent": PROJECT_USER_AGENT}
        )
        with urllib.request.urlopen(request) as response:
            return yaml.safe_load(response.read().decode("utf-8"))

    def fetch_catalog(self) -> list[ModInfo]:
        update_catalog = self._fetch_yaml(EVEREST_UPDATE_URL)
        search_catalog = self._fetch_yaml(MOD_SEARCH_DATABASE_URL)
        if not isinstance(update_catalog, Mapping):
            raise CatalogFormatError("Everest update catalog must be an object")
        if not isinstance(search_catalog, list):
            raise CatalogFormatError("Everest search catalog must be a list")

        search_metadata = self._index_search_catalog(search_catalog)
        mods: list[ModInfo] = []
        for name, entry in update_catalog.items():
            try:
                mods.append(self._normalize_entry(name, entry, search_metadata))
            except CatalogFormatError as e:
                logger.warning(f"Skipping invalid GameBanana catalog entry: {e}")
        if not mods:
            raise CatalogFormatError("the GameBanana catalog contains no valid entries")
        return mods

    @staticmethod
    def _index_search_catalog(
        search_catalog: list[object],
    ) -> dict[str, tuple[str | None, int | None]]:
        metadata: dict[str, tuple[str | None, int | None]] = {}
        for submission in search_catalog:
            if not isinstance(submission, Mapping):
                continue
            page_url = submission.get("PageURL")
            if not isinstance(page_url, str):
                page_url = None
            files = submission.get("Files")
            if not isinstance(files, list):
                continue
            for file_entry in files:
                if not isinstance(file_entry, Mapping):
                    continue
                url = file_entry.get("URL")
                if not isinstance(url, str):
                    continue
                match = _GAMEBANANA_DOWNLOAD_RE.fullmatch(url)
                if match is None:
                    continue
                downloads = file_entry.get("Downloads")
                if isinstance(downloads, bool) or not isinstance(downloads, int):
                    downloads = None
                metadata[match.group("file_id")] = (page_url, downloads)
        return metadata

    def _normalize_entry(
        self,
        name: object,
        entry: object,
        search_metadata: Mapping[str, tuple[str | None, int | None]],
    ) -> ModInfo:
        if not isinstance(name, str) or not name:
            raise CatalogFormatError("entry has an invalid internal mod name")
        if not isinstance(entry, Mapping):
            raise CatalogFormatError(f"'{name}' entry must be an object")
        version = entry.get("Version")
        hashes = entry.get("xxHash")
        download_url = entry.get("URL")
        size = entry.get("Size")
        file_id = entry.get("GameBananaFileId")
        if not isinstance(version, str) or not version:
            raise CatalogFormatError(f"'{name}' has an invalid version")
        if (
            not isinstance(hashes, list)
            or not hashes
            or not all(isinstance(value, str) for value in hashes)
        ):
            raise CatalogFormatError(f"'{name}' has an invalid xxHash list")
        if not isinstance(download_url, str):
            raise CatalogFormatError(f"'{name}' has no download URL")
        match = _GAMEBANANA_DOWNLOAD_RE.fullmatch(download_url)
        if match is None:
            raise CatalogFormatError(
                f"'{name}' has a non-official GameBanana download URL"
            )
        if isinstance(file_id, bool) or not isinstance(file_id, (str, int)):
            raise CatalogFormatError(f"'{name}' has an invalid GameBanana file ID")
        normalized_file_id = str(file_id)
        if normalized_file_id != match.group("file_id"):
            raise CatalogFormatError(
                f"'{name}' GameBanana file ID does not match its URL"
            )
        if size is not None and (
            isinstance(size, bool) or not isinstance(size, int) or size < 0
        ):
            raise CatalogFormatError(f"'{name}' has an invalid download size")

        page_url, downloads = search_metadata.get(normalized_file_id, (None, None))
        return ModInfo(
            source=self.name,
            name=name,
            version=version,
            xxhashes=tuple(hashes),
            download_url=download_url,
            size=size,
            page_url=page_url,
            downloads=downloads,
            remote_file_id=normalized_file_id,
        )

    def build_download_request(self, mod_info: ModInfo) -> urllib.request.Request:
        self._ensure_matching_source(mod_info)
        if _GAMEBANANA_DOWNLOAD_RE.fullmatch(mod_info.download_url) is None:
            raise CatalogFormatError(
                "mod information contains a non-official GameBanana URL"
            )
        return urllib.request.Request(
            mod_info.download_url,
            headers={
                "User-Agent": PROJECT_USER_AGENT,
                "Accept": "application/octet-stream",
            },
        )
