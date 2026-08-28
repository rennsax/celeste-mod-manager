import urllib.request
from collections.abc import Iterable

from loguru import logger

from .. import config
from .base import (
    CatalogFormatError,
    InvalidModSourceError,
    ModInfo,
    ModSourceBackend,
    ModSourceMismatchError,
    ModSourceName,
)
from .gamebanana import GameBananaModSource
from .wegfan import WegfanModSource

_BACKENDS: dict[ModSourceName, ModSourceBackend] = {
    ModSourceName.WEGFAN: WegfanModSource(),
    ModSourceName.GAMEBANANA: GameBananaModSource(),
}


def _resolve_source(configured_source: str | None) -> ModSourceName:
    if not configured_source:
        return ModSourceName.WEGFAN
    try:
        return ModSourceName(configured_source.strip().lower())
    except (AttributeError, ValueError) as e:
        valid_sources = ", ".join(source.value for source in ModSourceName)
        raise InvalidModSourceError(
            f"invalid mod source '{configured_source}'; expected one of: "
            f"{valid_sources}"
        ) from e


def configure(source_override: str | None = None) -> ModSourceName:
    configured_source = (
        source_override if source_override is not None else config.MOD_SOURCE
    )
    source = _resolve_source(configured_source)
    config.MOD_SOURCE = source.value
    logger.debug(f"Selected mod source: {source.value}")
    return source


def get_current_source() -> ModSourceName:
    return _resolve_source(config.MOD_SOURCE)


def _get_current_backend() -> ModSourceBackend:
    return _BACKENDS[get_current_source()]


def get_cache_filename() -> str:
    return _get_current_backend().cache_filename


def get_mod_db(*, force_update: bool = False) -> list[ModInfo]:
    return _get_current_backend().get_mod_db(force_update=force_update)


def get_cached_mod_db() -> list[ModInfo]:
    return _get_current_backend().get_cached_mod_db()


def index_mod_infos(mods: Iterable[ModInfo]) -> dict[str, ModInfo]:
    return _get_current_backend().index_mod_infos(mods)


def get_mod_info(name: str) -> ModInfo | None:
    return _get_current_backend().get_mod_info(name)


def search_mod_by_name(query: str) -> list[ModInfo]:
    return _get_current_backend().search_mod_by_name(query)


def pretty_print_mod_info(mod_info: ModInfo) -> None:
    _get_current_backend().pretty_print_mod_info(mod_info)


def get_download_request(mod_info: ModInfo) -> urllib.request.Request:
    backend = _get_current_backend()
    if mod_info.source != backend.name:
        raise ModSourceMismatchError(
            f"current mod source is '{backend.name.value}', but mod "
            f"'{mod_info.name}' belongs to '{mod_info.source.value}'"
        )
    return backend.build_download_request(mod_info)


__all__ = [
    "CatalogFormatError",
    "InvalidModSourceError",
    "ModInfo",
    "ModSourceBackend",
    "ModSourceMismatchError",
    "ModSourceName",
    "configure",
    "get_cache_filename",
    "get_cached_mod_db",
    "get_current_source",
    "get_download_request",
    "get_mod_db",
    "get_mod_info",
    "index_mod_infos",
    "pretty_print_mod_info",
    "search_mod_by_name",
]
