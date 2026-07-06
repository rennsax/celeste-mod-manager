import os
import sys
import urllib.request
import time
from enum import Enum
import yaml
from loguru import logger

from . import config
from .mod import Mod
from .mod_db import ModInfo, get_mod_info


def _format_size(num_bytes: int | float | None) -> str:
    if not num_bytes or num_bytes < 0:
        return "unknown size"

    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _make_download_reporthook(expected_size: int | None = None):
    start_time = time.monotonic()
    last_rendered_percent = -1

    def reporthook(block_count: int, block_size: int, total_size: int):
        nonlocal last_rendered_percent

        downloaded = block_count * block_size
        effective_total = total_size if total_size and total_size > 0 else expected_size
        if effective_total and effective_total > 0:
            downloaded = min(downloaded, effective_total)
            percent = min(100, int(downloaded * 100 / effective_total))
            if percent == last_rendered_percent and percent != 100:
                return
            last_rendered_percent = percent

            filled = int(30 * percent / 100)
            bar = "█" * filled + "░" * (30 - filled)
            elapsed = max(time.monotonic() - start_time, 0.001)
            speed = _format_size(downloaded / elapsed) + "/s"
            print(
                "\r"
                f"  {bar} {percent:3d}% "
                f"{_format_size(downloaded)}/{_format_size(effective_total)} "
                f"{speed}",
                end="",
                flush=True,
            )
            if percent >= 100:
                print()
        else:
            print(f"\r  Downloaded {_format_size(downloaded)}", end="", flush=True)

    return reporthook


def _download_mod(mod_info: ModInfo) -> Mod | None:
    if not mod_info or not mod_info.submissionFile:
        logger.critical("Invalid mod info provided for download.")
        sys.exit(1)

    url = mod_info.submissionFile.url
    filename = f"{mod_info.name}-{mod_info.version}.zip"
    filepath = os.path.join(config.MODS_DIR, filename)

    os.makedirs(config.MODS_DIR, exist_ok=True)

    expected_size = mod_info.submissionFile.size
    print(f"Collecting {mod_info.name}")
    print(f"  Downloading {filename} ({_format_size(expected_size)})")
    logger.debug(f"Downloading '{mod_info.name}' from '{url}'...")
    try:
        urllib.request.urlretrieve(
            url, filepath, reporthook=_make_download_reporthook(expected_size)
        )
        print(f"  Saved {filename}")
        logger.debug(f"Downloaded '{filename}' successfully.")
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


def get_mod_dependency_closure(
    mod: Mod, optional: bool = False, _visited: set[str] | None = None
) -> list[Mod]:
    installed_mods = get_installed_mods()
    installed_dict = {
        installed_mod.name: installed_mod for installed_mod in installed_mods
    }
    return _get_mod_dependency_closure_from_installed_dict(
        mod, installed_dict, optional=optional, _visited=_visited
    )


def _get_mod_dependency_closure_from_installed_dict(
    mod: Mod,
    installed_dict: dict[str, Mod],
    optional: bool = False,
    _visited: set[str] | None = None,
) -> list[Mod]:
    if _visited is None:
        _visited = set()

    if mod.name in _visited:
        return []
    _visited.add(mod.name)

    closure = [mod]
    for dep in mod.get_mod_deps(optional=optional):
        dep_name = dep.get("Name")
        if (
            not dep_name
            or dep_name in ["Everest", "Celeste", "EverestCore"]
            or dep_name not in installed_dict
        ):
            continue

        closure.extend(
            _get_mod_dependency_closure_from_installed_dict(
                installed_dict[dep_name],
                installed_dict,
                optional=optional,
                _visited=_visited,
            )
        )

    return closure


def get_mods_exclusively_depending_on_closure(mod: Mod) -> list[Mod]:
    installed_mods = get_installed_mods()
    installed_dict = {
        installed_mod.name: installed_mod for installed_mod in installed_mods
    }
    root_names = {root_mod.name for root_mod in get_root_mods()}

    reverse_graph: dict[str, set[str]] = {
        installed_mod.name: set() for installed_mod in installed_mods
    }
    for installed_mod in installed_mods:
        for dep in installed_mod.get_mod_deps(optional=True):
            dep_name = dep.get("Name")
            if (
                not dep_name
                or dep_name in ["Everest", "Celeste", "EverestCore"]
                or dep_name not in installed_dict
            ):
                continue
            reverse_graph[dep_name].add(installed_mod.name)

    closure_names = {
        closure_mod.name
        for closure_mod in get_mod_dependency_closure(mod, optional=True)
    }
    changed = True
    while changed:
        changed = False
        for name in list(closure_names):
            if name == mod.name:
                continue
            if name in root_names or not reverse_graph.get(name, set()).issubset(
                closure_names
            ):
                closure_names.remove(name)
                changed = True

    result = [installed_dict[mod.name]]
    result.extend(
        sorted(
            (installed_dict[name] for name in closure_names if name != mod.name),
            key=lambda installed_mod: installed_mod.name.lower(),
        )
    )
    return result


def resolve_deps(
    mod: Mod,
    optional: bool = False,
    _visited: set | None = None,
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

        print(f"  Resolving dependency {dep_name} ({dep_version})")
        installed_mods = get_installed_mods()
        found_mods = [m for m in installed_mods if m.name == dep_name]

        if len(found_mods) > 1:
            logger.error(
                f"Multiple mods found for dependency '{dep_name}': {found_mods}"
            )
            failed_deps.append(dep_name)
        elif len(found_mods) == 1:
            dep_mod = found_mods[0]
            print(
                f"  Requirement already satisfied: {dep_mod.name} "
                f"({dep_mod.version})"
            )
            if dep_mod.version != dep_version:
                logger.warning(
                    f"Version mismatch for '{dep_name}': required {dep_version}, found {dep_mod.version}"
                )
            sub_resolved, sub_failed = resolve_deps(
                dep_mod,
                optional=optional,
                _visited=_visited,
            )
            resolved_deps.extend(sub_resolved)
            failed_deps.extend(sub_failed)
        else:
            print(f"  Downloading dependency {dep_name}")
            logger.debug(
                f"Dependency '{dep_name}' not found locally. Try to resolve..."
            )
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
                    dep_mod,
                    optional=optional,
                    _visited=_visited,
                )
                resolved_deps.extend(sub_resolved)
                failed_deps.extend(sub_failed)
            else:
                logger.error(f"Failed to download dependency '{dep_name}'.")
                failed_deps.append(dep_name)

    return resolved_deps, failed_deps


def get_disabled_required_mods(mod: Mod, optional: bool = False) -> list[Mod]:
    installed_mods = get_installed_mods()
    installed_dict = {
        installed_mod.name: installed_mod for installed_mod in installed_mods
    }
    blacklisted_filenames = get_blacklisted_mod_filenames()
    closure = _get_mod_dependency_closure_from_installed_dict(
        mod, installed_dict, optional=optional
    )
    return sorted(
        (
            closure_mod
            for closure_mod in closure
            if not _is_mod_enabled(closure_mod, blacklisted_filenames)
        ),
        key=lambda closure_mod: closure_mod.name.lower(),
    )


def pretty_print_mods(mods: list[Mod], show_enabled: bool = True):
    if not mods or len(mods) == 0:
        print("No mods installed.")
        return

    mods.sort(key=lambda m: m.name.lower())
    blacklisted_filenames = get_blacklisted_mod_filenames()
    statuses_by_name = {
        mod.name: "ON" if _is_mod_enabled(mod, blacklisted_filenames) else ""
        for mod in mods
    }

    max_name_len = max([len("Package")] + [len(mod.name) for mod in mods])
    max_version_len = max([len("Version")] + [len(mod.version) for mod in mods])
    max_status_len = max(
        [len("Enabled")] + [len(status) for status in statuses_by_name.values()]
    )

    if show_enabled:
        print(
            f"{'Mod':<{max_name_len}} "
            f"{'Version':<{max_version_len}} "
            f"{'Enabled':<{max_status_len}}"
        )
        print(f"{'-' * max_name_len} {'-' * max_version_len} {'-' * max_status_len}")
    else:
        print(f"{'Mod':<{max_name_len}} {'Version':<{max_version_len}}")
        print(f"{'-' * max_name_len} {'-' * max_version_len}")

    for mod in mods:
        status = statuses_by_name[mod.name]
        if show_enabled:
            print(
                f"{mod.name:<{max_name_len}} "
                f"{mod.version:<{max_version_len}} "
                f"{status:^{max_status_len}}"
            )
        else:
            print(f"{mod.name:<{max_name_len}} {mod.version:<{max_version_len}}")


def analyse_mod_deps(maxdepth: int, optional: bool = False, enabled_only: bool = False):
    mods = get_installed_mods()
    if not mods:
        print("No mods installed.")
        return

    blacklisted_filenames = get_blacklisted_mod_filenames()
    installed_dict = {mod.name: mod for mod in mods}
    graph = {}
    in_degree = {mod.name: 0 for mod in mods}
    optional_dependents: dict[str, set[str]] = {mod.name: set() for mod in mods}

    for mod in mods:
        graph[mod.name] = []
        required_deps = mod.get_mod_deps(optional=False)
        required_names = {d.get("Name") for d in required_deps if d.get("Name")}
        optional_deps = [
            dep
            for dep in mod.get_mod_deps(optional=True)
            if dep.get("Name") not in required_names
        ]
        deps = required_deps + (optional_deps if optional else [])

        for dep in optional_deps:
            dep_name = dep.get("Name")
            if dep_name in installed_dict and (
                not enabled_only or _is_mod_enabled(mod, blacklisted_filenames)
            ):
                optional_dependents[dep_name].add(mod.name)

        for dep in deps:
            dep_name = dep["Name"]
            if not dep_name or dep_name in ["Everest", "Celeste", "EverestCore"]:
                continue

            is_opt = dep_name not in required_names

            if dep_name in installed_dict:
                graph[mod.name].append((dep_name, is_opt))
                if (not is_opt or optional) and (
                    not enabled_only or _is_mod_enabled(mod, blacklisted_filenames)
                ):
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
    if enabled_only:
        roots = [
            root
            for root in roots
            if _is_mod_enabled(installed_dict[root], blacklisted_filenames)
        ]
        if not roots:
            print("No mods installed.")
            return
    roots.sort(key=lambda x: x.lower())

    recorded_root_names = set()
    if config._ENABLE_ROOT_INSTALL_TRACK:
        recorded_root_names = {mod.name for mod in get_root_mods()}
    orphan_roots = (
        {
            root
            for root in roots
            if root not in recorded_root_names and not optional_dependents[root]
        }
        if config._ENABLE_ROOT_INSTALL_TRACK
        else set()
    )

    def print_tree(
        node, prefix="", is_last=True, is_root=False, is_opt=False, current_depth=1
    ):
        if is_root:
            display_node = f"{node} ({installed_dict[node].version})"
            if not _is_mod_enabled(installed_dict[node], blacklisted_filenames):
                display_node = f"{display_node} \033[91m[DISABLED]\033[0m"
            if node in orphan_roots:
                display_node = f"{display_node} \033[1;33m[ORPHAN]\033[0m"
            if node in optional_dependents and optional_dependents[node]:
                dependents = ", ".join(sorted(optional_dependents[node], key=str.lower))
                display_node = f"{display_node} (optionally depended by {dependents})"
            print(display_node)
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
                if node in installed_dict and not _is_mod_enabled(
                    installed_dict[node], blacklisted_filenames
                ):
                    display_node = f"{display_node} \033[91m[DISABLED]\033[0m"
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

    if orphan_roots:
        print()
        print("\033[1;33mWARNING:\033[0m Orphan root mod(s) detected:")
        for mod_name in sorted(orphan_roots, key=str.lower):
            print(f"  - {mod_name}")
        print(
            "Use `celeste-mod-manager install MOD...' to record them as root mods,\n"
            " or `celeste-mod-manager uninstall MOD...' to remove them."
        )


class EnsureModStatus(Enum):
    INSTALLED = "installed"
    ALREADY_EXISTS = "already_exists"
    NOT_FOUND_IN_DB = "not_found_in_db"
    DOWNLOAD_FAILED = "download_failed"
    UNEXPECTED = "unexpected"


def _get_installed_mods_record_path(for_write: bool = False) -> str:
    installed_mods_path = os.path.join(config.MODS_DIR, "installed_mods.yml")
    return installed_mods_path


def _record_root_installed_mod(mod: Mod) -> None:
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return

    logger.debug(f"Try to record root mod '{mod.name}' with version '{mod.version}'.")
    installed_mods_path = _get_installed_mods_record_path(for_write=True)

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

    root_entry = {
        "name": mod.name,
        "version": mod.version,
        "filename": mod.get_filename(),
    }

    for recorded_mod in roots:
        if not isinstance(recorded_mod, dict):
            logger.warning(
                f"Invalid entry in 'root' list in '{installed_mods_path}': {recorded_mod}"
            )
            continue
        if recorded_mod.get("name") == mod.name:
            logger.debug(
                f"Mod '{mod.name}' already recorded as root mod. Updating version to '{mod.version}'."
            )
            recorded_mod.update(root_entry)
            break
    else:
        logger.debug(
            f"Recording '{mod.name}' as a new root mod with version '{mod.version}'."
        )
        roots.append(root_entry)

    data["root"] = roots
    with open(installed_mods_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _remove_root_installed_mods(mod_names: set[str]) -> None:
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return

    installed_mods_path = _get_installed_mods_record_path()
    if not os.path.exists(installed_mods_path):
        return

    with open(installed_mods_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        logger.warning(
            f"Unexpected format in '{installed_mods_path}'. No root mods removed."
        )
        return

    roots = data.get("root", [])
    if not isinstance(roots, list):
        logger.warning(
            f"Unexpected format for 'root' in '{installed_mods_path}'. No root mods removed."
        )
        return

    data["root"] = [
        entry
        for entry in roots
        if not isinstance(entry, dict) or entry.get("name") not in mod_names
    ]

    with open(installed_mods_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def get_root_mods() -> list[Mod]:
    installed_mods_path = _get_installed_mods_record_path()
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
                matched_mod = None
                filename = entry.get("filename")
                if isinstance(filename, str):
                    matched_mod = Mod.from_filename(filename)
                    if matched_mod and (
                        matched_mod.name != entry["name"]
                        or matched_mod.version != entry["version"]
                    ):
                        logger.warning(
                            f"Root mod '{filename}' metadata does not match recorded entry '{entry['name']}' with version '{entry['version']}'."
                        )
                        matched_mod = None
                if not matched_mod:
                    matched_mod = next(
                        (
                            mod
                            for mod in installed_mods
                            if mod.name == entry["name"]
                            and mod.version == entry["version"]
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


class UninstallModStatus(Enum):
    READY = "ready"
    ROOT_TRACK_DISABLED = "root_track_disabled"
    NOT_INSTALLED = "not_installed"
    NOT_RECORDED_ROOT = "not_recorded_root"
    UNEXPECTED = "unexpected"


_BLACKLIST_HEADER = (
    "# This is the blacklist. Lines starting with # are ignored.",
    "# Mod folders and archives listed in this file will be disabled.",
    "# ExampleFolder",
    "# SomeMod.zip",
    "",
    "# The following blacklist is generated by celeste-mod-manager.",
)


def _get_blacklist_path() -> str:
    return os.path.join(config.MODS_DIR, "blacklist.txt")


def _read_blacklist_lines() -> list[str]:
    blacklist_path = _get_blacklist_path()
    if not os.path.exists(blacklist_path):
        return []

    with open(blacklist_path, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f]


def get_blacklisted_mod_filenames() -> set[str]:
    filenames = set()
    for line in _read_blacklist_lines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        filenames.add(entry)
    return filenames


def _write_blacklist_filenames(
    filenames_to_add: set[str] | None = None,
    filenames_to_remove: set[str] | None = None,
) -> None:
    filenames_to_add = filenames_to_add or set()
    filenames_to_remove = filenames_to_remove or set()

    os.makedirs(config.MODS_DIR, exist_ok=True)

    entries = set()
    for line in _read_blacklist_lines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            if entry in filenames_to_remove:
                continue
            entries.add(entry)

    entries.update(filenames_to_add)
    new_lines = list(_BLACKLIST_HEADER)
    new_lines.extend(sorted(entries, key=str.lower))

    blacklist_path = _get_blacklist_path()
    with open(blacklist_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines).rstrip() + "\n")


def _is_mod_enabled(mod: Mod, blacklisted_filenames: set[str]) -> bool:
    return mod.get_filename() not in blacklisted_filenames


def _get_mod_dependency_closure_from_available_mods(
    mod: Mod,
    available_mods: list[Mod],
    optional: bool = False,
    _visited: set[str] | None = None,
) -> list[Mod]:
    available_dict = {
        available_mod.name: available_mod for available_mod in available_mods
    }
    return _get_mod_dependency_closure_from_installed_dict(
        mod, available_dict, optional=optional, _visited=_visited
    )


class ModToggleStatus(Enum):
    READY = "ready"
    ROOT_TRACK_DISABLED = "root_track_disabled"
    NOT_INSTALLED = "not_installed"
    NOT_RECORDED_ROOT = "not_recorded_root"
    ALREADY_ENABLED = "already_enabled"
    ALREADY_DISABLED = "already_disabled"
    UNEXPECTED = "unexpected"


def build_disable_plan(mod_name: str) -> tuple[list[Mod], ModToggleStatus]:
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return [], ModToggleStatus.ROOT_TRACK_DISABLED

    try:
        installed_mods = get_installed_mods()
        installed_dict = {mod.name: mod for mod in installed_mods}
        if mod_name not in installed_dict:
            return [], ModToggleStatus.NOT_INSTALLED

        root_names = {mod.name for mod in get_root_mods()}
        if mod_name not in root_names:
            return [], ModToggleStatus.NOT_RECORDED_ROOT

        blacklisted_filenames = get_blacklisted_mod_filenames()
        target_mod = installed_dict[mod_name]
        if not _is_mod_enabled(target_mod, blacklisted_filenames):
            return [], ModToggleStatus.ALREADY_DISABLED

        enabled_mods = [
            mod for mod in installed_mods if _is_mod_enabled(mod, blacklisted_filenames)
        ]
        enabled_dict = {mod.name: mod for mod in enabled_mods}
        reverse_graph: dict[str, set[str]] = {mod.name: set() for mod in enabled_mods}
        for enabled_mod in enabled_mods:
            for dep in enabled_mod.get_mod_deps(optional=True):
                dep_name = dep.get("Name")
                if (
                    not dep_name
                    or dep_name in ["Everest", "Celeste", "EverestCore"]
                    or dep_name not in enabled_dict
                ):
                    continue
                reverse_graph[dep_name].add(enabled_mod.name)

        closure_names = {
            closure_mod.name
            for closure_mod in _get_mod_dependency_closure_from_available_mods(
                target_mod, enabled_mods, optional=True
            )
        }
        changed = True
        while changed:
            changed = False
            for name in list(closure_names):
                if name == target_mod.name:
                    continue
                if name in root_names or not reverse_graph.get(name, set()).issubset(
                    closure_names
                ):
                    closure_names.remove(name)
                    changed = True

        result = [target_mod]
        result.extend(
            sorted(
                (
                    enabled_dict[name]
                    for name in closure_names
                    if name != target_mod.name
                ),
                key=lambda mod: mod.name.lower(),
            )
        )
        return result, ModToggleStatus.READY
    except Exception as e:
        logger.error(f"Failed to build disable plan for mod '{mod_name}': {e}")
        return [], ModToggleStatus.UNEXPECTED


def build_enable_plan(
    mod_name: str, optional: bool = False
) -> tuple[list[Mod], ModToggleStatus]:
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return [], ModToggleStatus.ROOT_TRACK_DISABLED

    try:
        installed_mods = get_installed_mods()
        installed_dict = {mod.name: mod for mod in installed_mods}
        if mod_name not in installed_dict:
            return [], ModToggleStatus.NOT_INSTALLED

        root_names = {mod.name for mod in get_root_mods()}
        if mod_name not in root_names:
            return [], ModToggleStatus.NOT_RECORDED_ROOT

        target_mod = installed_dict[mod_name]
        blacklisted_filenames = get_blacklisted_mod_filenames()
        closure = _get_mod_dependency_closure_from_installed_dict(
            target_mod, installed_dict, optional=optional
        )
        mods_to_enable = [
            mod for mod in closure if not _is_mod_enabled(mod, blacklisted_filenames)
        ]
        if not mods_to_enable:
            return [], ModToggleStatus.ALREADY_ENABLED

        return mods_to_enable, ModToggleStatus.READY
    except Exception as e:
        logger.error(f"Failed to build enable plan for mod '{mod_name}': {e}")
        return [], ModToggleStatus.UNEXPECTED


def disable_mods(mods: list[Mod]) -> bool:
    try:
        _write_blacklist_filenames({mod.get_filename() for mod in mods})
        return True
    except Exception as e:
        logger.error(f"Failed to disable mods: {e}")
        return False


def enable_mods(mods: list[Mod]) -> bool:
    try:
        _write_blacklist_filenames(
            filenames_to_remove={mod.get_filename() for mod in mods}
        )
        return True
    except Exception as e:
        logger.error(f"Failed to enable mods: {e}")
        return False


def build_uninstall_plan(
    mod_name: str, force: bool = False
) -> tuple[list[Mod], UninstallModStatus]:
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return [], UninstallModStatus.ROOT_TRACK_DISABLED

    try:
        installed_mods = get_installed_mods()
        installed_dict = {mod.name: mod for mod in installed_mods}
        if mod_name not in installed_dict:
            return [], UninstallModStatus.NOT_INSTALLED

        root_names = {mod.name for mod in get_root_mods()}
        if mod_name not in root_names:
            if force:
                return [installed_dict[mod_name]], UninstallModStatus.READY
            return [], UninstallModStatus.NOT_RECORDED_ROOT

        return (
            get_mods_exclusively_depending_on_closure(installed_dict[mod_name]),
            UninstallModStatus.READY,
        )
    except Exception as e:
        logger.error(f"Failed to build uninstall plan for mod '{mod_name}': {e}")
        return [], UninstallModStatus.UNEXPECTED


def uninstall_mods(mods: list[Mod]) -> bool:
    try:
        root_names = {mod.name for mod in get_root_mods()}
        filenames_to_remove = {mod.get_filename() for mod in mods}
        for mod in mods:
            if os.path.exists(mod.filepath):
                os.remove(mod.filepath)
            else:
                logger.warning(f"Mod file '{mod.filepath}' does not exist.")
        _remove_root_installed_mods(
            {mod.name for mod in mods if mod.name in root_names}
        )
        _write_blacklist_filenames(filenames_to_remove=filenames_to_remove)
        return True
    except Exception as e:
        logger.error(f"Failed to uninstall mods: {e}")
        return False


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
