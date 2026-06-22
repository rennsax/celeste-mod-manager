import os
import sys
import urllib.request
from enum import Enum
import yaml
from loguru import logger

from . import config
from .mod import Mod
from .mod_db import ModInfo, get_mod_info


def _download_mod(mod_info: ModInfo) -> Mod | None:
    if not mod_info or not mod_info.submissionFile:
        logger.critical("Invalid mod info provided for download.")
        sys.exit(1)

    url = mod_info.submissionFile.url
    filename = f"{mod_info.name}-{mod_info.version}.zip"
    filepath = os.path.join(config.MODS_DIR, filename)

    os.makedirs(config.MODS_DIR, exist_ok=True)

    logger.info(f"Downloading '{mod_info.name}' from '{url}'...")
    try:
        urllib.request.urlretrieve(url, filepath)
        logger.info(f"Downloaded '{filename}' successfully.")
        mod = Mod.from_filename(filename)
        if not mod:
            logger.error(f"Downloaded '{filename}' is not a valid mod archive.")
            return None
        if mod.name != mod_info.name or mod.version != mod_info.version:
            logger.warning(
                f"Downloaded mod metadata mismatch for '{filename}': "
                f"database={mod_info.name} {mod_info.version}, "
                f"archive={mod.name} {mod.version}"
            )
        return mod
    except Exception as e:
        logger.error(f"Failed to download '{filename}': {e}")
        # remove the file if it was partially downloaded
        if os.path.exists(filepath):
            os.remove(filepath)
        return None


def get_installed_mods() -> list[Mod]:
    if not os.path.exists(config.MODS_DIR):
        return []
    mods = []
    files = [f for f in os.listdir(config.MODS_DIR) if f != "Cache"]
    for filename in files:
        filepath = os.path.join(config.MODS_DIR, filename)
        if os.path.isfile(filepath) and filename.lower().endswith(".zip"):
            mod = Mod.from_filename(filename)
            if mod:
                mods.append(mod)
    return mods


def resolve_deps(
    mod: Mod, optional: bool = False, _visited: set | None = None
) -> tuple[list[Mod], list[str]]:
    if _visited is None:
        _visited = set()

    if mod.name in _visited:
        return [], []
    _visited.add(mod.name)

    deps = mod.get_mod_deps(optional=optional)
    resolved_deps = []
    failed_deps = []

    if not os.path.exists(config.MODS_DIR):
        os.makedirs(config.MODS_DIR, exist_ok=True)

    for dep in deps:
        dep_name = dep["Name"]
        dep_version = dep["Version"]

        if dep_name in ["Everest", "Celeste", "EverestCore"]:
            logger.debug(f"Skipping dependency '{dep_name}' as it's a core component.")
            continue

        if dep_name in _visited:
            continue

        installed_mods = get_installed_mods()
        found_mods = [m for m in installed_mods if m.name == dep_name]

        if len(found_mods) > 1:
            logger.error(
                f"Multiple mods found for dependency '{dep_name}': {found_mods}"
            )
            failed_deps.append(dep_name)
        elif len(found_mods) == 1:
            dep_mod = found_mods[0]
            if dep_mod.version != dep_version:
                logger.warning(
                    f"Version mismatch for '{dep_name}': required {dep_version}, found {dep_mod.version}"
                )
            sub_resolved, sub_failed = resolve_deps(
                dep_mod, optional=optional, _visited=_visited
            )
            resolved_deps.extend(sub_resolved)
            failed_deps.extend(sub_failed)
        else:
            logger.info(f"Dependency '{dep_name}' not found locally. Try to resolve...")
            mod_info = get_mod_info(dep_name)
            if not mod_info:
                logger.error(f"Dependency '{dep_name}' not found in the database.")
                failed_deps.append(dep_name)
                continue
            dep_mod = _download_mod(mod_info)
            if dep_mod:
                if dep_mod.version != dep_version:
                    logger.warning(
                        f"Version mismatch for downloaded '{dep_name}': required {dep_version}, got {dep_mod.version}"
                    )
                resolved_deps.append(dep_mod)
                sub_resolved, sub_failed = resolve_deps(
                    dep_mod, optional=optional, _visited=_visited
                )
                resolved_deps.extend(sub_resolved)
                failed_deps.extend(sub_failed)
            else:
                logger.error(f"Failed to download dependency '{dep_name}'.")
                failed_deps.append(dep_name)

    return resolved_deps, failed_deps


def pretty_print_mods(mods: list[Mod]):
    if not mods or len(mods) == 0:
        print("No mods installed.")
        return

    mods.sort(key=lambda m: m.name.lower())

    max_name_len = max([len("Package")] + [len(mod.name) for mod in mods])
    max_version_len = max([len("Version")] + [len(mod.version) for mod in mods])

    print(f"{'Mod':<{max_name_len}} {'Version':<{max_version_len}}")
    print(f"{'-' * max_name_len} {'-' * max_version_len}")
    for mod in mods:
        print(f"{mod.name:<{max_name_len}} {mod.version:<{max_version_len}}")


def analyse_mod_deps(maxdepth: int, optional: bool = False):
    mods = get_installed_mods()
    if not mods:
        print("No mods installed.")
        return

    installed_dict = {mod.name: mod for mod in mods}
    graph = {}
    in_degree = {mod.name: 0 for mod in mods}

    for mod in mods:
        graph[mod.name] = []
        deps = mod.get_mod_deps(optional=optional)
        required_deps = mod.get_mod_deps(optional=False)
        required_names = {d.get("Name") for d in required_deps if d.get("Name")}

        for dep in deps:
            dep_name = dep["Name"]
            if not dep_name or dep_name in ["Everest", "Celeste", "EverestCore"]:
                continue

            is_opt = dep_name not in required_names

            if dep_name in installed_dict:
                graph[mod.name].append((dep_name, is_opt))
                if not is_opt:
                    in_degree[dep_name] = in_degree.get(dep_name, 0) + 1
            else:
                graph[mod.name].append((f"{dep_name} (Missing)", is_opt))

    # Check whether the graph has cycles
    visited = {}

    def has_cycle(node, path):
        visited[node] = 1
        path.append(node)
        for neighbor, is_opt in graph.get(node, []):
            if neighbor.endswith(" (Missing)"):
                continue
            if visited.get(neighbor) == 1:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                logger.critical(f"Dependency cycle detected: {' -> '.join(cycle)}")
                return True
            if visited.get(neighbor, 0) == 0:
                if has_cycle(neighbor, path):
                    return True
        path.pop()
        visited[node] = 2
        return False

    for node in graph:
        if visited.get(node, 0) == 0:
            if has_cycle(node, []):
                logger.critical("Cycle detected in the dependency graph.")
                sys.exit(1)

    # Find the sources in the DAG
    roots = [node for node in graph if in_degree.get(node) == 0]
    roots.sort(key=lambda x: x.lower())

    def print_tree(
        node, prefix="", is_last=True, is_root=False, is_opt=False, current_depth=1
    ):
        if is_root:
            print(f"{node} ({installed_dict[node].version})")
            new_prefix = prefix
        else:
            connector = "└── " if is_last else "├── "
            if node.endswith(" (Missing)"):
                display_node = f"\033[91m{node}\033[0m"
            else:
                display_node = (
                    f"{node} ({installed_dict[node].version})"
                    if node in installed_dict
                    else node
                )
            if is_opt:
                display_node = f"{display_node} (Optional)"
            print(f"{prefix}{connector}{display_node}")
            new_prefix = prefix + ("    " if is_last else "│   ")

        if current_depth >= maxdepth:
            return

        children = sorted(graph.get(node, []), key=lambda x: x[0].lower())
        for i, (child, child_is_opt) in enumerate(children):
            is_last_child = i == len(children) - 1
            print_tree(
                child,
                new_prefix,
                is_last_child,
                is_root=False,
                is_opt=child_is_opt,
                current_depth=current_depth + 1,
            )

    for i in range(len(roots)):
        print_tree(roots[i], is_root=True)
        if i < len(roots) - 1:
            print()


class EnsureModStatus(Enum):
    INSTALLED = "installed"
    ALREADY_EXISTS = "already_exists"
    NOT_FOUND_IN_DB = "not_found_in_db"
    DOWNLOAD_FAILED = "download_failed"
    UNEXPECTED = "unexpected"


def _record_root_installed_mod(mod: Mod) -> None:
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return

    logger.debug(f"Try to record root mod '{mod.name}' with version '{mod.version}'.")
    installed_mods_path = os.path.join(config.MODS_DIR, "installed_mods.yaml")

    data: dict = {}
    roots = None
    if os.path.exists(installed_mods_path):
        with open(installed_mods_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded
            else:
                logger.warning(
                    f"Unexpected format in '{installed_mods_path}'. Attempt to overwrite it with new data."
                )
        roots = data.get("root")
        if not isinstance(roots, list):
            logger.warning(
                f"Unexpected format in '{installed_mods_path}'. Attempt to overwrite it with new data."
            )
            roots = []
    else:
        roots = []

    for recorded_mod in roots:
        if recorded_mod["name"] == mod.name:
            logger.debug(
                f"Mod '{mod.name}' already recorded as root mod. Updating version to '{mod.version}'."
            )
            recorded_mod["version"] = mod.version
            break
    else:
        logger.debug(
            f"Recording '{mod.name}' as a new root mod with version '{mod.version}'."
        )
        roots.append({"name": mod.name, "version": mod.version})

    data["root"] = roots
    with open(installed_mods_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def get_root_mods() -> list[Mod]:
    installed_mods_path = os.path.join(config.MODS_DIR, "installed_mods.yaml")
    if not os.path.exists(installed_mods_path):
        return []

    with open(installed_mods_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning(
                f"Unexpected format in '{installed_mods_path}'. No root mods loaded."
            )
            return []
        roots = data.get("root", [])
        if not isinstance(roots, list):
            logger.warning(
                f"Unexpected format for 'root' in '{installed_mods_path}'. No root mods loaded."
            )
            return []
        mods = []
        installed_mods = get_installed_mods()
        for entry in roots:
            if isinstance(entry, dict) and "name" in entry and "version" in entry:
                matched_mod = next(
                    (
                        mod
                        for mod in installed_mods
                        if mod.name == entry["name"] and mod.version == entry["version"]
                    ),
                    None,
                )
                if matched_mod:
                    mods.append(matched_mod)
                else:
                    logger.warning(
                        f"Root mod '{entry['name']}' with version '{entry['version']}' is not installed."
                    )
            else:
                logger.warning(
                    f"Invalid entry in 'root' list in '{installed_mods_path}': {entry}"
                )
        return mods


def ensure_mod(mod_name: str, root: bool = False) -> tuple[Mod | None, EnsureModStatus]:
    """Ensure that a mod with the given name is installed. If it's already installed, return it. If not, try to download and install it. If root is True, also record it as a root mod."""
    try:
        mods = get_installed_mods()
        for mod in mods:
            if mod.name == mod_name:
                try:
                    _record_root_installed_mod(mod)
                except Exception as e:
                    logger.error(f"Failed to record root mod '{mod.name}': {e}")
                    raise e
                return mod, EnsureModStatus.ALREADY_EXISTS

        mod_info = get_mod_info(mod_name)
        if not mod_info:
            logger.info(f"Mod '{mod_name}' not found in the database.")
            return None, EnsureModStatus.NOT_FOUND_IN_DB

        mod = _download_mod(mod_info)
        if mod is None:
            return None, EnsureModStatus.DOWNLOAD_FAILED
        if root:
            try:
                _record_root_installed_mod(mod)
            except Exception as e:
                logger.error(f"Failed to record root mod '{mod.name}': {e}")
                raise e
        return mod, EnsureModStatus.INSTALLED
    except Exception as e:
        logger.error(f"Failed to ensure mod '{mod_name}': {e}")
        return None, EnsureModStatus.UNEXPECTED


class UpdateModStatus(Enum):
    UPDATED = "updated"
    ALREADY_UP_TO_DATE = "already_up_to_date"
    DOWNLOAD_FAILED = "download_failed"
    UNEXPECTED = "unexpected"


def update_mod(mod: Mod) -> tuple[Mod | None, UpdateModStatus]:
    """Check if there's an update for the given mod. If there is, download and install it. Return the updated mod (or the original mod if it's already up to date) and the status."""
    root_mods = get_root_mods() if config._ENABLE_ROOT_INSTALL_TRACK else []
    try:
        mod_info = get_mod_info(mod.name)
        if not mod_info:
            logger.info(f"Mod '{mod.name}' not found in the database.")
            return None, UpdateModStatus.UNEXPECTED

        if mod_info.version == mod.version:
            return mod, UpdateModStatus.ALREADY_UP_TO_DATE

        updated_mod = _download_mod(mod_info)
        if updated_mod is None:
            return None, UpdateModStatus.DOWNLOAD_FAILED
        if updated_mod.filepath != mod.filepath:
            os.remove(mod.filepath)
        for root_mod in root_mods:
            if root_mod.name == mod.name:
                _record_root_installed_mod(updated_mod)
                break
        return updated_mod, UpdateModStatus.UPDATED
    except Exception as e:
        logger.error(f"Failed to update mod '{mod.name}': {e}")
        return None, UpdateModStatus.UNEXPECTED
