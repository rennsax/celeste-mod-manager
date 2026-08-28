import json
import os
import tempfile
import time
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

from .. import config
from ..path import get_mod_db_path

CACHE_SCHEMA_VERSION = 1


class InvalidModSourceError(ValueError):
    """Raised when a configured mod source is unknown."""


class ModSourceMismatchError(ValueError):
    """Raised when data from one source is used with another source."""


class CatalogFormatError(ValueError):
    """Raised when a remote or cached catalog has an invalid structure."""


class ModSourceName(str, Enum):
    WEGFAN = "wegfan"
    GAMEBANANA = "gamebanana"


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CatalogFormatError(f"'{field_name}' must be a string or null")
    return value


def _optional_integer(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogFormatError(f"'{field_name}' must be an integer or null")
    if value < 0:
        raise CatalogFormatError(f"'{field_name}' must not be negative")
    return value


@dataclass(frozen=True)
class ModInfo:
    source: ModSourceName
    name: str
    version: str
    xxhashes: tuple[str, ...]
    download_url: str
    size: int | None
    page_url: str | None
    downloads: int | None
    remote_file_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.source, ModSourceName):
            raise CatalogFormatError("'source' must be a ModSourceName")
        if not isinstance(self.name, str) or not self.name:
            raise CatalogFormatError("'name' must be a non-empty string")
        if not isinstance(self.version, str) or not self.version:
            raise CatalogFormatError("'version' must be a non-empty string")
        if not isinstance(self.xxhashes, tuple) or not all(
            isinstance(value, str) for value in self.xxhashes
        ):
            raise CatalogFormatError("'xxhashes' must be a tuple of strings")
        if not isinstance(self.download_url, str) or not self.download_url:
            raise CatalogFormatError("'download_url' must be a non-empty string")
        _optional_integer(self.size, "size")
        _optional_string(self.page_url, "page_url")
        _optional_integer(self.downloads, "downloads")
        _optional_string(self.remote_file_id, "remote_file_id")

    def to_cache_dict(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "name": self.name,
            "version": self.version,
            "xxhashes": list(self.xxhashes),
            "download_url": self.download_url,
            "size": self.size,
            "page_url": self.page_url,
            "downloads": self.downloads,
            "remote_file_id": self.remote_file_id,
        }

    @classmethod
    def from_cache_dict(cls, data: Mapping[str, object]) -> "ModInfo":
        if not isinstance(data, Mapping):
            raise CatalogFormatError("mod catalog entry must be an object")

        try:
            source_value = data["source"]
            name = data["name"]
            version = data["version"]
            xxhashes_value = data["xxhashes"]
            download_url = data["download_url"]
        except KeyError as e:
            raise CatalogFormatError(
                f"mod catalog entry is missing required field '{e.args[0]}'"
            ) from e

        try:
            source = ModSourceName(source_value)
        except (TypeError, ValueError) as e:
            raise CatalogFormatError(f"invalid mod source '{source_value}'") from e
        if not isinstance(name, str) or not name:
            raise CatalogFormatError("'name' must be a non-empty string")
        if not isinstance(version, str) or not version:
            raise CatalogFormatError("'version' must be a non-empty string")
        if not isinstance(download_url, str) or not download_url:
            raise CatalogFormatError("'download_url' must be a non-empty string")
        if not isinstance(xxhashes_value, list) or not all(
            isinstance(value, str) for value in xxhashes_value
        ):
            raise CatalogFormatError("'xxhashes' must be a list of strings")

        return cls(
            source=source,
            name=name,
            version=version,
            xxhashes=tuple(xxhashes_value),
            download_url=download_url,
            size=_optional_integer(data.get("size"), "size"),
            page_url=_optional_string(data.get("page_url"), "page_url"),
            downloads=_optional_integer(data.get("downloads"), "downloads"),
            remote_file_id=_optional_string(
                data.get("remote_file_id"), "remote_file_id"
            ),
        )


class ModSourceBackend(ABC):
    name: ModSourceName
    cache_filename: str

    @abstractmethod
    def fetch_catalog(self) -> list[ModInfo]:
        """Fetch and normalize the complete remote catalog without caching it."""

    @abstractmethod
    def build_download_request(self, mod_info: ModInfo) -> urllib.request.Request:
        """Build a download request for a ModInfo owned by this backend."""

    def _ensure_matching_source(self, mod_info: ModInfo) -> None:
        if mod_info.source != self.name:
            raise ModSourceMismatchError(
                f"cannot use {mod_info.source.value} mod information with the "
                f"{self.name.value} backend"
            )

    def _cache_path(self) -> Path:
        return get_mod_db_path(self.cache_filename)

    def _read_cache_document(self) -> tuple[float, list[ModInfo]]:
        cache_path = self._cache_path()
        try:
            with cache_path.open("r", encoding="utf-8") as cache_file:
                document = json.load(cache_file)
        except (OSError, json.JSONDecodeError) as e:
            raise CatalogFormatError(
                f"failed to read local {self.name.value} mod database: {e}"
            ) from e

        if not isinstance(document, dict):
            raise CatalogFormatError("the local mod database must be an object")
        if document.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise CatalogFormatError(
                "the local mod database has an unsupported schema version"
            )
        if document.get("source") != self.name.value:
            raise ModSourceMismatchError(
                f"the local mod database belongs to source "
                f"'{document.get('source')}', not '{self.name.value}'"
            )
        last_update_time = document.get("last_update_time")
        if isinstance(last_update_time, bool) or not isinstance(
            last_update_time, (int, float)
        ):
            raise CatalogFormatError(
                "the local mod database does not contain a valid last update time"
            )
        entries = document.get("data")
        if not isinstance(entries, list):
            raise CatalogFormatError(
                "the local mod database does not contain a valid data list"
            )
        mods = [ModInfo.from_cache_dict(entry) for entry in entries]
        self._validate_catalog(mods)
        return float(last_update_time), mods

    def _validate_catalog(self, mods: Iterable[ModInfo]) -> None:
        for mod_info in mods:
            if not isinstance(mod_info, ModInfo):
                raise CatalogFormatError(
                    f"the {self.name.value} backend returned a non-ModInfo entry"
                )
            self._ensure_matching_source(mod_info)

    def _publish_cache(self, mods: list[ModInfo]) -> None:
        cache_path = self._cache_path()
        document = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source": self.name.value,
            "last_update_time": time.time(),
            "data": [mod_info.to_cache_dict() for mod_info in mods],
        }
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{self.cache_filename}.",
                suffix=".tmp",
                dir=cache_path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(document, temporary_file, ensure_ascii=False)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, cache_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def get_mod_db(
        self, *, force_update: bool = config.FORCE_UPDATE_DEFAULT
    ) -> list[ModInfo]:
        cached_mods: list[ModInfo] | None = None
        needs_update = force_update or not self._cache_path().exists()

        if not needs_update:
            try:
                last_update_time, cached_mods = self._read_cache_document()
                needs_update = (
                    time.time() - last_update_time
                ) / 86400 > config.DB_UPDATE_PERIOD_DAYS
            except Exception as e:
                logger.warning(
                    f"Failed to read or parse local {self.name.value} mod db: {e}. "
                    "Will force an update."
                )
                needs_update = True

        if not needs_update:
            assert cached_mods is not None
            return cached_mods

        print("Updating the local mod database...")
        try:
            mods = self.fetch_catalog()
            if not isinstance(mods, list):
                raise CatalogFormatError(
                    f"the {self.name.value} backend returned a non-list catalog"
                )
            self._validate_catalog(mods)
            self._publish_cache(mods)
            return mods
        except Exception as refresh_error:
            if force_update:
                raise
            if cached_mods is None:
                try:
                    _, cached_mods = self._read_cache_document()
                except Exception:
                    raise refresh_error
            logger.warning(
                f"Failed to refresh the {self.name.value} mod database. "
                "Using the existing same-source cache."
            )
            return cached_mods

    def get_cached_mod_db(self) -> list[ModInfo]:
        _, mods = self._read_cache_document()
        return mods

    def index_mod_infos(self, mods: Iterable[ModInfo]) -> dict[str, ModInfo]:
        mod_list = list(mods)
        self._validate_catalog(mod_list)
        return {mod_info.name: mod_info for mod_info in mod_list}

    def get_mod_info(self, name: str) -> ModInfo | None:
        return self.index_mod_infos(self.get_mod_db()).get(name)

    @staticmethod
    def _compact_mod_name(name: str) -> str:
        return "".join(
            character for character in name.casefold() if character.isalnum()
        )

    @staticmethod
    def _fuzzy_name_score(query: str, candidate: str) -> float:
        window_radius = 1 if len(query) < 6 else 2
        minimum_window_size = max(1, len(query) - window_radius)
        maximum_window_size = min(len(candidate), len(query) + window_radius)
        if maximum_window_size < minimum_window_size:
            return 0.0
        return max(
            fuzz.ratio(query, candidate[start : start + window_size])
            for window_size in range(minimum_window_size, maximum_window_size + 1)
            for start in range(len(candidate) - window_size + 1)
        )

    def search_mod_by_name(self, query: str) -> list[ModInfo]:
        mods = self.get_mod_db()
        compact_query = self._compact_mod_name(query)
        if not compact_query:
            return []

        direct_matches: list[tuple[int, ModInfo]] = []
        fuzzy_candidates: list[tuple[ModInfo, str]] = []
        for mod in mods:
            compact_name = self._compact_mod_name(mod.name)
            if compact_name == compact_query:
                direct_matches.append((0, mod))
            elif compact_name.startswith(compact_query):
                direct_matches.append((1, mod))
            elif compact_query in compact_name:
                direct_matches.append((2, mod))
            else:
                fuzzy_candidates.append((mod, compact_name))

        direct_matches.sort(key=lambda match: (match[0], match[1].name.casefold()))
        found_mods = [mod for _, mod in direct_matches]
        if found_mods:
            return found_mods
        if len(compact_query) < 4:
            return []

        ranked_fuzzy_mods: list[tuple[float, ModInfo]] = []
        for mod, compact_name in fuzzy_candidates:
            score = self._fuzzy_name_score(compact_query, compact_name)
            if score >= 80:
                ranked_fuzzy_mods.append((score, mod))
        ranked_fuzzy_mods.sort(key=lambda match: (-match[0], match[1].name.casefold()))
        return [mod for _, mod in ranked_fuzzy_mods[:5]]

    def pretty_print_mod_info(self, mod_info: ModInfo) -> None:
        self._ensure_matching_source(mod_info)
        print(f"Name: {mod_info.name}")
        print(f"Version: {mod_info.version}")
        print(f"Page URL: {mod_info.page_url or 'unavailable'}")
        print(f"Download URL: {mod_info.download_url}")
        if mod_info.size is None:
            size_str = "unknown"
        else:
            size_kb = mod_info.size / 1024
            size_str = f"{size_kb:.2f} KB"
            if size_kb > 1024:
                size_mb = size_kb / 1024
                size_str = f"{size_mb:.2f} MB"
                if size_mb > 1024:
                    size_str = f"{size_mb / 1024:.2f} GB"
        print(f"File Size: {size_str}")
        downloads = (
            str(mod_info.downloads) if mod_info.downloads is not None else "unavailable"
        )
        print(f"Downloads: {downloads}")
