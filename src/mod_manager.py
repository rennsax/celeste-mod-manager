import os
import sys
import urllib.request
from typing import Optional, List, Tuple
from loguru import logger

from .config import *
from .mod import Mod
from .mod_db import ModInfo, get_mod_info

def _download_mod(mod_info: ModInfo) -> Optional[Mod]:
    if not mod_info or not mod_info.submissionFile:
        logger.critical("Invalid mod info provided for download.")
        sys.exit(1)
    url = mod_info.submissionFile.url
    filename = f"{mod_info.name}-{mod_info.version}.zip"
    filepath = os.path.join(MODS_DIR, filename)

    if os.path.exists(filepath):
        logger.info(f"Mod '{filename}' already exists. Skipping download.")
        return Mod(name=mod_info.name, version=mod_info.version)

    os.makedirs(MODS_DIR, exist_ok=True)

    logger.info(f"Downloading '{mod_info.name}' from '{url}'...")
    try:
        urllib.request.urlretrieve(url, filepath)
        logger.info(f"Downloaded '{filename}' successfully.")
        return Mod(name=mod_info.name, version=mod_info.version)
    except Exception as e:
        logger.error(f"Failed to download '{filename}': {e}")
        # remove the file if it was partially downloaded
        if os.path.exists(filepath):
            os.remove(filepath)
        return None

def resolve_deps(mod: Mod, optional: bool = False, _visited: Optional[set] = None) -> Tuple[List[Mod], List[str]]:
    if _visited is None:
        _visited = set()

    if mod.name in _visited:
        return [], []
    _visited.add(mod.name)

    deps = mod.get_mod_deps(optional=optional)
    resolved_deps = []
    failed_deps = []

    if not os.path.exists(MODS_DIR):
        os.makedirs(MODS_DIR, exist_ok=True)

    for dep in deps:
        dep_name = dep['Name']
        dep_version = dep['Version']

        if dep_name in ["Everest", "Celeste", "EverestCore"]:
            logger.debug(f"Skipping dependency '{dep_name}' as it's a core component.")
            continue

        if dep_name in _visited:
            continue

        found_files = [f for f in os.listdir(MODS_DIR) if f.startswith(f"{dep_name}-") and f.endswith(".zip")]

        if len(found_files) > 1:
            logger.error(f"Multiple files found for dependency '{dep_name}': {found_files}")
            failed_deps.append(dep_name)
        elif len(found_files) == 1:
            filename = found_files[0]
            dep_mod = Mod.from_filename(filename)
            if not dep_mod:
                logger.error(f"Failed to parse mod from filename '{filename}'.")
                failed_deps.append(dep_name)
                continue
            if dep_mod and dep_mod.version != dep_version:
                logger.warning(f"Version mismatch for '{dep_name}': required {dep_version}, found {dep_mod.version}")
            if dep_mod:
                resolved_deps.append(dep_mod)
                sub_resolved, sub_failed = resolve_deps(dep_mod, optional=optional, _visited=_visited)
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
                    logger.warning(f"Version mismatch for downloaded '{dep_name}': required {dep_version}, got {dep_mod.version}")
                resolved_deps.append(dep_mod)
                sub_resolved, sub_failed = resolve_deps(dep_mod, optional=optional, _visited=_visited)
                resolved_deps.extend(sub_resolved)
                failed_deps.extend(sub_failed)
            else:
                logger.error(f"Failed to download dependency '{dep_name}'.")
                failed_deps.append(dep_name)

    return resolved_deps, failed_deps

def get_installed_mods() -> List[Mod]:
    if not os.path.exists(MODS_DIR):
        return []
    mods = []
    for filename in os.listdir(MODS_DIR):
        if filename.endswith(".zip"):
            mod = Mod.from_filename(filename)
            if mod:
                mods.append(mod)
    return mods

def pretty_print_mods(mods: List[Mod]):
    if not mods or len(mods) == 0:
        print("No mods installed.")
        return

    mods.sort(key=lambda m: m.name.lower())

    max_name_len = max([len("Package")] + [len(mod.name) for mod in mods])
    max_version_len = max([len("Version")] + [len(mod.version) for mod in mods])

    print(f"{'Package':<{max_name_len}} {'Version':<{max_version_len}}")
    print(f"{'-' * max_name_len} {'-' * max_version_len}")
    for mod in mods:
        print(f"{mod.name:<{max_name_len}} {mod.version:<{max_version_len}}")

def analyse_mod_deps(maxdepth: Optional[int] = None, optional: bool = False):
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
        required_names = {d.get('Name') for d in required_deps if d.get('Name')}

        for dep in deps:
            dep_name = dep['Name']
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

    def print_tree(node, prefix="", is_last=True, is_root=False, is_opt=False, current_depth=1):
        if is_root:
            print(f"{node} ({installed_dict[node].version})")
            new_prefix = prefix
        else:
            connector = "└── " if is_last else "├── "
            if node.endswith(" (Missing)"):
                display_node = f"\033[91m{node}\033[0m"
            else:
                display_node = f"{node} ({installed_dict[node].version})" if node in installed_dict else node
            if is_opt:
                display_node = f"{display_node} (Optional)"
            print(f"{prefix}{connector}{display_node}")
            new_prefix = prefix + ("    " if is_last else "│   ")

        if maxdepth is not None and maxdepth != 0 and current_depth >= maxdepth:
            return

        children = sorted(graph.get(node, []), key=lambda x: x[0].lower())
        for i, (child, child_is_opt) in enumerate(children):
            is_last_child = (i == len(children) - 1)
            print_tree(child, new_prefix, is_last_child, is_root=False, is_opt=child_is_opt, current_depth=current_depth + 1)

    for i in range(len(roots)):
        print_tree(roots[i], is_root=True)
        if i < len(roots) - 1:
            print()