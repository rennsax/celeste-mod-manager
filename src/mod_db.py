import os
import urllib.request
import json
import time
from typing import Callable, Any
from dataclasses import dataclass
from loguru import logger

from . import config

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
    def from_dict(cls, data: dict) -> 'ModSubmission':
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
    def from_dict(cls, data: dict) -> 'ModSubmissionFile':
        data_copy = data.copy()
        if 'submission' in data_copy and data_copy['submission']:
            data_copy['submission'] = ModSubmission.from_dict(data_copy['submission'])
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
    def from_dict(cls, data: dict) -> 'ModInfo':
        data_copy = data.copy()
        if 'submissionFile' in data_copy and data_copy['submissionFile']:
            data_copy['submissionFile'] = ModSubmissionFile.from_dict(data_copy['submissionFile'])
        return cls(**data_copy)

def get_mod_db(url: str, force_update: bool = config.FORCE_UPDATE_DEFAULT) -> dict:
    needs_update = force_update or not os.path.exists(config.MOD_DB_PATH)

    if not needs_update:
        try:
            with open(config.MOD_DB_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                last_update_ts = data.get("lastUpdateTime")
                if last_update_ts is not None:
                    if (time.time() - last_update_ts) / 86400 > config.DB_UPDATE_PERIOD_DAYS:
                        needs_update = True
                else:
                    needs_update = True
        except Exception as e:
            logger.warning(f"Failed to read or parse local mod db: {e}. Will force to update.")
            needs_update = True

    if needs_update:
        logger.info("Downloading mod list from the server...")
        with urllib.request.urlopen(url) as response:
            db_data = json.loads(response.read().decode('utf-8'))
        db_data["lastUpdateTime"] = time.time()

        with open(config.MOD_DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(db_data, f, ensure_ascii=False)

    with open(config.MOD_DB_PATH, 'r', encoding='utf-8') as f:
        return json.load(f).get("data", [])

def get_mod_info(mod_name: str, force_update: bool = config.FORCE_UPDATE_DEFAULT) -> ModInfo | None:
    mod_list = get_mod_db(f"{config.WEGFAN_API_URL}/mod/list", force_update=force_update)
    for mod in mod_list:
        if mod.get("name") == mod_name:
            return ModInfo.from_dict(mod)
    return None

def search_mod_in_db(predicate: Callable[[ModInfo], Any], auto_update: bool = config.FORCE_UPDATE_DEFAULT) -> list[ModInfo]:
    mod_list = get_mod_db(f"{config.WEGFAN_API_URL}/mod/list", force_update=auto_update)
    founded_mods = []
    for mod_info_dict in mod_list:
        mod_info = ModInfo.from_dict(mod_info_dict)
        if predicate(mod_info):
            founded_mods.append(mod_info)
    return founded_mods

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
