import json
import urllib.request
from collections.abc import Mapping
from urllib.parse import urlsplit

from loguru import logger

from .base import CatalogFormatError, ModInfo, ModSourceBackend, ModSourceName

WEGFAN_API_URL = "https://celeste.weg.fan/api/v2"


class WegfanModSource(ModSourceBackend):
    name = ModSourceName.WEGFAN
    cache_filename = "celeste_mod_db.wegfan.json"

    def fetch_catalog(self) -> list[ModInfo]:
        with urllib.request.urlopen(f"{WEGFAN_API_URL}/mod/list") as response:
            document = json.loads(response.read().decode("utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            raise CatalogFormatError("the WEGFAN catalog does not contain a data list")

        mods: list[ModInfo] = []
        for entry in document["data"]:
            try:
                mods.append(self._normalize_entry(entry))
            except CatalogFormatError as e:
                logger.warning(f"Skipping invalid WEGFAN catalog entry: {e}")
        if not mods:
            raise CatalogFormatError("the WEGFAN catalog contains no valid entries")
        return mods

    def _normalize_entry(self, entry: object) -> ModInfo:
        if not isinstance(entry, Mapping):
            raise CatalogFormatError("entry must be an object")
        submission_file = entry.get("submissionFile")
        if not isinstance(submission_file, Mapping):
            raise CatalogFormatError("entry is missing submissionFile")
        submission = submission_file.get("submission")
        if not isinstance(submission, Mapping):
            submission = {}

        name = entry.get("name")
        version = entry.get("version")
        hashes = entry.get("xxHash")
        download_url = submission_file.get("url")
        size = submission_file.get("size")
        if not isinstance(name, str) or not name:
            raise CatalogFormatError("entry has an invalid internal mod name")
        if not isinstance(version, str) or not version:
            raise CatalogFormatError(f"'{name}' has an invalid version")
        if (
            not isinstance(hashes, list)
            or not hashes
            or not all(isinstance(value, str) for value in hashes)
        ):
            raise CatalogFormatError(f"'{name}' has an invalid xxHash list")
        if not isinstance(download_url, str) or not self._is_wegfan_url(download_url):
            raise CatalogFormatError(f"'{name}' has an invalid WEGFAN download URL")
        if size is not None and (
            isinstance(size, bool) or not isinstance(size, int) or size < 0
        ):
            raise CatalogFormatError(f"'{name}' has an invalid download size")

        page_url = submission.get("pageUrl")
        downloads = submission_file.get("downloads")
        remote_file_id = submission_file.get("id")
        if not isinstance(page_url, str):
            page_url = None
        if isinstance(downloads, bool) or not isinstance(downloads, int):
            downloads = None
        return ModInfo(
            source=self.name,
            name=name,
            version=version,
            xxhashes=tuple(hashes),
            download_url=download_url,
            size=size,
            page_url=page_url,
            downloads=downloads,
            remote_file_id=(
                str(remote_file_id) if remote_file_id is not None else None
            ),
        )

    @staticmethod
    def _is_wegfan_url(url: str) -> bool:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.hostname == "celeste.weg.fan"

    def build_download_request(self, mod_info: ModInfo) -> urllib.request.Request:
        self._ensure_matching_source(mod_info)
        if not self._is_wegfan_url(mod_info.download_url):
            raise CatalogFormatError("mod information contains an invalid WEGFAN URL")
        return urllib.request.Request(mod_info.download_url)
