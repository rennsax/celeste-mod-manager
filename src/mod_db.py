import urllib.request
import json
import time
from dataclasses import dataclass
from loguru import logger
from rapidfuzz import fuzz

from . import config
from .path import get_mod_db_path


@dataclass
class ModSubmission:
    id: str
    createTime: str
    updateTime: str
    name: str
    submissionType: str
    submitter: str
    pageUrl: str
    gameBananaSection: str
    gameBananaId: int
    categoryId: int
    categoryName: str
    latestUpdateAddedTime: str
    deleteTime: str | None = None
    subCategoryId: int | None = None
    subCategoryName: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ModSubmission":
        return cls(**data)


@dataclass
class ModSubmissionFile:
    id: str
    createTime: str
    updateTime: str
    url: str
    description: str
    downloads: int
    size: int
    gameBananaId: int
    submission: ModSubmission
    deleteTime: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ModSubmissionFile":
        data_copy = data.copy()
        if "submission" in data_copy and data_copy["submission"]:
            data_copy["submission"] = ModSubmission.from_dict(data_copy["submission"])
        return cls(**data_copy)


@dataclass
class ModInfo:
    id: str
    createTime: str
    updateTime: str
    name: str
    version: str
    xxHash: list[str]
    submissionFile: ModSubmissionFile
    deleteTime: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ModInfo":
        data_copy = data.copy()
        if "submissionFile" in data_copy and data_copy["submissionFile"]:
            data_copy["submissionFile"] = ModSubmissionFile.from_dict(
                data_copy["submissionFile"]
            )
        return cls(**data_copy)


def get_cached_mod_db() -> list[dict]:
    """Read the cached mod database without applying the refresh TTL."""
    with get_mod_db_path().open("r", encoding="utf-8") as f:
        cached_data = json.load(f)

    mod_list = cached_data.get("data")
    if not isinstance(mod_list, list):
        raise ValueError("the local mod database does not contain a valid data list")
    return mod_list


def index_mod_infos(mod_list: list[dict]) -> dict[str, ModInfo]:
    """Parse database entries into a name-indexed mapping."""
    return {
        mod_info.name: mod_info
        for mod_info in (ModInfo.from_dict(entry) for entry in mod_list)
    }


def get_mod_db(
    url: str, force_update: bool = config.FORCE_UPDATE_DEFAULT
) -> list[dict]:
    mod_db_path = get_mod_db_path()
    needs_update = force_update or not mod_db_path.exists()

    if not needs_update:
        try:
            with mod_db_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                last_update_ts = data.get("lastUpdateTime")
                if last_update_ts is not None:
                    if (
                        time.time() - last_update_ts
                    ) / 86400 > config.DB_UPDATE_PERIOD_DAYS:
                        needs_update = True
                else:
                    needs_update = True
        except Exception as e:
            logger.warning(
                f"Failed to read or parse local mod db: {e}. Will force to update."
            )
            needs_update = True

    if needs_update:
        print("Updating the local mod database...")
        with urllib.request.urlopen(url) as response:
            db_data = json.loads(response.read().decode("utf-8"))
        db_data["lastUpdateTime"] = time.time()

        with mod_db_path.open("w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False)

    return get_cached_mod_db()


def get_mod_info(mod_name: str) -> ModInfo | None:
    mod_list = get_mod_db(f"{config.WEGFAN_API_URL}/mod/list")
    for mod in mod_list:
        if mod.get("name") == mod_name:
            return ModInfo.from_dict(mod)
    return None


def _compact_mod_name(name: str) -> str:
    """Normalize a name and remove separators for direct matching."""
    return "".join(character for character in name.casefold() if character.isalnum())


def _fuzzy_name_score(query: str, candidate: str) -> float:
    """Score a compact name against similarly sized windows in a candidate."""
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


def search_mod_by_name(query: str) -> list[ModInfo]:
    """Search mod names using direct matching followed by fuzzy matching."""
    mod_list = get_mod_db(f"{config.WEGFAN_API_URL}/mod/list")
    mods = [ModInfo.from_dict(mod_info_dict) for mod_info_dict in mod_list]

    compact_query = _compact_mod_name(query)
    if not compact_query:
        return []

    direct_matches: list[tuple[int, ModInfo]] = []
    fuzzy_candidates: list[tuple[ModInfo, str]] = []

    for mod in mods:
        compact_name = _compact_mod_name(mod.name)
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

    ranked_fuzzy_mods = []
    for mod, compact_name in fuzzy_candidates:
        score = _fuzzy_name_score(compact_query, compact_name)
        if score >= 80:
            ranked_fuzzy_mods.append((score, mod))
    ranked_fuzzy_mods.sort(key=lambda match: (-match[0], match[1].name.casefold()))

    return [mod for _, mod in ranked_fuzzy_mods[:5]]


def pretty_print_mod_info(mod_info: ModInfo):
    print(f"Name: {mod_info.name}")
    print(f"Version: {mod_info.version}")
    print(f"Page URL: {mod_info.submissionFile.submission.pageUrl}")
    print(f"Download URL: {mod_info.submissionFile.url}")
    size_bytes = mod_info.submissionFile.size
    size_kb = size_bytes / 1024
    size_str = f"{size_kb:.2f} KB"
    if size_kb > 1024:
        size_mb = size_kb / 1024
        size_str = f"{size_mb:.2f} MB"
        if size_mb > 1024:
            size_gb = size_mb / 1024
            size_str = f"{size_gb:.2f} GB"
    print(f"File Size: {size_str}")
    print(f"Downloads: {mod_info.submissionFile.downloads}")
