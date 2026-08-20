import os
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum

import yaml
from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn

from . import config
from .mod import Mod
from .mod_db import ModInfo, get_mod_info

_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_PROGRESS_WIDTH = 30


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


@contextmanager
def _download_progress(expected_size: int | None = None):
    start_time = time.monotonic()
    initial_total = expected_size if expected_size and expected_size > 0 else None
    initial_size = (
        f"0 B/{_format_size(initial_total)}"
        if initial_total is not None
        else "Downloaded 0 B"
    )
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=_DOWNLOAD_PROGRESS_WIDTH),
        TextColumn("{task.fields[percent]}"),
        TextColumn("{task.fields[size]}"),
        TextColumn("{task.fields[speed]}"),
        console=Console(file=sys.stdout, soft_wrap=True),
        refresh_per_second=5,
    )
    task_id = progress.add_task(
        " ",
        total=initial_total,
        percent="  0%" if initial_total is not None else "",
        size=initial_size,
        speed="0 B/s" if initial_total is not None else "",
    )

    def reporthook(block_count: int, block_size: int, total_size: int):
        downloaded = max(0, block_count * block_size)
        effective_total = total_size if total_size and total_size > 0 else expected_size
        if effective_total and effective_total > 0:
            downloaded = min(downloaded, effective_total)
            percent = min(100, int(downloaded * 100 / effective_total))
            elapsed = max(time.monotonic() - start_time, 0.001)
            progress.update(
                task_id,
                completed=downloaded,
                total=effective_total,
                percent=f"{percent:3d}%",
                size=(f"{_format_size(downloaded)}/{_format_size(effective_total)}"),
                speed=_format_size(downloaded / elapsed) + "/s",
            )
        else:
            progress.update(
                task_id,
                completed=downloaded,
                total=None,
                percent="",
                size=f"Downloaded {_format_size(downloaded)}",
                speed="",
            )

    with progress:
        yield reporthook


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
    last_error: Exception | None = None

    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        temporary_filepath: str | None = None

        try:
            fd, temporary_filepath = tempfile.mkstemp(
                prefix=f".{filename}.", suffix=".download.zip", dir=config.MODS_DIR
            )
            os.close(fd)
            temporary_filename = os.path.basename(temporary_filepath)
            with _download_progress(expected_size) as reporthook:
                urllib.request.urlretrieve(
                    url,
                    temporary_filepath,
                    reporthook=reporthook,
                )
            mod = Mod.from_filename(temporary_filename)
            if not mod:
                raise ValueError("downloaded file is not a valid mod archive")

            os.replace(temporary_filepath, filepath)
            mod.filepath = filepath
            print(f"  Saved {filename}")
            logger.debug(f"Downloaded '{filename}' successfully.")
            if mod.name != mod_info.name or mod.version != mod_info.version:
                logger.warning(
                    f"Downloaded mod metadata mismatch for '{filename}': "
                    f"database={mod_info.name} {mod_info.version}, "
                    f"archive={mod.name} {mod.version}"
                )
            return mod
        except Exception as e:
            last_error = e
            logger.warning(
                f"Download attempt {attempt}/{_DOWNLOAD_MAX_ATTEMPTS} "
                f"for '{filename}' failed: {e}"
            )
        finally:
            if temporary_filepath and os.path.exists(temporary_filepath):
                try:
                    os.remove(temporary_filepath)
                except OSError as e:
                    logger.warning(
                        f"Failed to remove temporary download "
                        f"'{temporary_filepath}': {e}"
                    )

    logger.error(
        f"Failed to download '{filename}' after {_DOWNLOAD_MAX_ATTEMPTS} attempts: "
        f"{last_error}"
    )
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
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return [mod]

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


def _print_dependency_tree(
    roots: list[str],
    graph: dict[str, list[tuple[str, bool]]],
    installed_dict: dict[str, Mod],
    maxdepth: int,
    *,
    blacklisted_filenames: set[str] | None = None,
    orphan_roots: set[str] | None = None,
    optional_dependents: dict[str, set[str]] | None = None,
    show_disabled: bool = False,
):
    blacklisted_filenames = blacklisted_filenames or set()
    orphan_roots = orphan_roots or set()
    optional_dependents = optional_dependents or {}

    def print_tree(
        node,
        prefix="",
        is_last=True,
        is_root=False,
        is_opt=False,
        current_depth=1,
        path=frozenset(),
    ):
        is_cycle = not is_root and node in path
        if is_root:
            display_node = f"{node} ({installed_dict[node].version})"
            if show_disabled and not _is_mod_enabled(
                installed_dict[node], blacklisted_filenames
            ):
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
                if (
                    show_disabled
                    and node in installed_dict
                    and not _is_mod_enabled(installed_dict[node], blacklisted_filenames)
                ):
                    display_node = f"{display_node} \033[91m[DISABLED]\033[0m"
            if is_opt:
                display_node = f"{display_node} (Optional)"
            if is_cycle:
                display_node = f"{display_node} [CYCLE]"
            print(f"{prefix}{connector}{display_node}")
            new_prefix = prefix + ("    " if is_last else "│   ")

        if is_cycle or current_depth >= maxdepth:
            return

        next_path = path | {node}
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
                path=next_path,
            )

    for i in range(len(roots)):
        print_tree(roots[i], is_root=True)
        if i < len(roots) - 1:
            print()


def _has_dependency_cycle(
    graph: dict[str, list[tuple[str, bool]]], *, include_optional: bool = True
) -> bool:
    visited = {}

    def has_cycle(node, path):
        visited[node] = 1
        path.append(node)
        for neighbor, is_opt in graph.get(node, []):
            if neighbor not in graph or (is_opt and not include_optional):
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
                return True
    return False


def _get_strongly_connected_components(
    graph: dict[str, list[tuple[str, bool]]],
) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def connect(node: str):
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        children = sorted(graph.get(node, []), key=lambda item: item[0].lower())
        for neighbor, _ in children:
            if neighbor not in graph:
                continue
            if neighbor not in indices:
                connect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] != indices[node]:
            return

        component = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        components.append(sorted(component, key=str.lower))

    for node in sorted(graph, key=str.lower):
        if node not in indices:
            connect(node)

    return sorted(components, key=lambda component: component[0].lower())


def _is_cyclic_component(
    component: list[str], graph: dict[str, list[tuple[str, bool]]]
) -> bool:
    if len(component) > 1:
        return True
    node = component[0]
    return any(neighbor == node for neighbor, _ in graph.get(node, []))


def _get_dependency_graph_roots(
    graph: dict[str, list[tuple[str, bool]]],
    root_edges: set[tuple[str, str]],
    eligible_roots: set[str] | None = None,
) -> list[str]:
    components = _get_strongly_connected_components(graph)
    component_by_node = {
        node: component_index
        for component_index, component in enumerate(components)
        for node in component
    }
    component_in_degree = [0] * len(components)

    for source, target in root_edges:
        source_component = component_by_node[source]
        target_component = component_by_node[target]
        if source_component != target_component:
            component_in_degree[target_component] += 1

    eligible_roots = eligible_roots if eligible_roots is not None else set(graph)
    roots = []
    for component_index, component in enumerate(components):
        if component_in_degree[component_index] != 0:
            continue
        candidates = [node for node in component if node in eligible_roots]
        if candidates:
            roots.append(min(candidates, key=str.lower))

    return sorted(roots, key=str.lower)


def _warn_about_optional_dependency_cycles(
    graph: dict[str, list[tuple[str, bool]]],
) -> None:
    for component in _get_strongly_connected_components(graph):
        if not _is_cyclic_component(component, graph):
            continue
        members = ", ".join(component)
        print(
            "WARNING: optional dependency cycle detected among: "
            f"{members}. Continuing because Everest permits cycles involving "
            "optional dependencies.",
            file=sys.stderr,
        )


def _analyse_enabled_mod_deps(maxdepth: int, optional: bool = False):
    mods = get_installed_mods()
    if not mods:
        print("No mods installed.")
        return

    blacklisted_filenames = get_blacklisted_mod_filenames()
    enabled_mods = [mod for mod in mods if _is_mod_enabled(mod, blacklisted_filenames)]
    if not enabled_mods:
        print("No mods installed.")
        return

    installed_dict = {mod.name: mod for mod in enabled_mods}
    graph = {}
    root_edges: set[tuple[str, str]] = set()

    for mod in enabled_mods:
        graph[mod.name] = []
        required_deps = mod.get_mod_deps(optional=False)
        required_names = {d.get("Name") for d in required_deps if d.get("Name")}
        optional_deps = [
            dep
            for dep in mod.get_mod_deps(optional=True)
            if dep.get("Name") not in required_names
        ]
        deps = required_deps + (optional_deps if optional else [])

        for dep in deps:
            dep_name = dep.get("Name")
            if not dep_name or dep_name in ["Everest", "Celeste", "EverestCore"]:
                continue
            if dep_name not in installed_dict:
                continue

            is_opt = dep_name not in required_names
            graph[mod.name].append((dep_name, is_opt))
            root_edges.add((mod.name, dep_name))

    if _has_dependency_cycle(graph, include_optional=False):
        logger.critical("Cycle detected in the dependency graph.")
        sys.exit(1)

    _warn_about_optional_dependency_cycles(graph)
    roots = _get_dependency_graph_roots(graph, root_edges)
    if not roots:
        print("No mods installed.")
        return

    _print_dependency_tree(
        roots,
        graph,
        installed_dict,
        maxdepth,
    )


def analyse_mod_deps(maxdepth: int, optional: bool = False, enabled_only: bool = False):
    if config._ENABLE_EXPERIMENTAL_APPLY:
        _analyse_enabled_mod_deps(maxdepth=maxdepth, optional=optional)
        return

    mods = get_installed_mods()
    if not mods:
        print("No mods installed.")
        return

    blacklisted_filenames = get_blacklisted_mod_filenames()
    installed_dict = {mod.name: mod for mod in mods}
    graph = {}
    root_edges: set[tuple[str, str]] = set()
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
                    root_edges.add((mod.name, dep_name))
            else:
                graph[mod.name].append((f"{dep_name} (Missing)", is_opt))

    if _has_dependency_cycle(graph, include_optional=False):
        logger.critical("Cycle detected in the dependency graph.")
        sys.exit(1)

    _warn_about_optional_dependency_cycles(graph)
    eligible_roots = (
        {
            mod.name
            for mod in mods
            if _is_mod_enabled(mod, blacklisted_filenames)
        }
        if enabled_only
        else None
    )
    roots = _get_dependency_graph_roots(graph, root_edges, eligible_roots)
    if not roots:
        print("No mods installed.")
        return

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

    _print_dependency_tree(
        roots,
        graph,
        installed_dict,
        maxdepth,
        blacklisted_filenames=blacklisted_filenames,
        orphan_roots=orphan_roots,
        optional_dependents=optional_dependents,
        show_disabled=True,
    )

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
    if not config._ENABLE_ROOT_INSTALL_TRACK:
        return []

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

_MOD_OPTIONS_ORDER_HEADER = (
    "# This is the Mod Options order file. Lines starting with # are ignored.",
    "# Mod folders and archives in this file will be displayed in the same order in the Mod Options menu.",
    '# To define the position of the "Everest Core" options, put "Everest" on a line.',
    "# ExampleFolder",
    "# SomeMod.zip",
    "",
    "# The following mod options order is generated by celeste-mod-manager.",
)


def _get_blacklist_path() -> str:
    return os.path.join(config.MODS_DIR, "blacklist.txt")


def _get_update_blacklist_path() -> str:
    return os.path.join(config.MODS_DIR, "updaterblacklist.txt")


def _get_mod_options_order_path() -> str:
    return os.path.join(config.MODS_DIR, "modoptionsorder.txt")


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


def get_update_blacklisted_mod_filenames() -> set[str]:
    update_blacklist_path = _get_update_blacklist_path()
    if not os.path.exists(update_blacklist_path):
        return set()

    with open(update_blacklist_path, "r", encoding="utf-8") as f:
        return {
            entry
            for line in f
            if (entry := line.strip()) and not entry.startswith("#")
        }


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


def _replace_blacklist_filenames(filenames: set[str]) -> None:
    os.makedirs(config.MODS_DIR, exist_ok=True)

    new_lines = list(_BLACKLIST_HEADER)
    new_lines.extend(sorted(filenames, key=str.lower))

    blacklist_path = _get_blacklist_path()
    with open(blacklist_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines).rstrip() + "\n")


def _replace_mod_options_order_entries(entries: list[str]) -> None:
    os.makedirs(config.MODS_DIR, exist_ok=True)

    new_lines = list(_MOD_OPTIONS_ORDER_HEADER)
    new_lines.extend(entries)

    mod_options_order_path = _get_mod_options_order_path()
    with open(mod_options_order_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines).rstrip() + "\n")


def _replace_mod_options_order_filename(
    old_filename: str, new_filename: str
) -> bool:
    """Replace existing archive entries without rewriting a user's order file."""
    mod_options_order_path = _get_mod_options_order_path()
    if not os.path.exists(mod_options_order_path):
        return False

    with open(mod_options_order_path, "r", encoding="utf-8", newline="") as f:
        lines = f.read().splitlines(keepends=True)

    replaced = False
    new_lines = []
    for line in lines:
        line_body = line.rstrip("\r\n")
        entry = line_body.strip()
        if entry == old_filename and not entry.startswith("#"):
            leading_whitespace = line_body[: len(line_body) - len(line_body.lstrip())]
            trailing_whitespace = line_body[len(line_body.rstrip()) :]
            line_ending = line[len(line_body) :]
            new_lines.append(
                f"{leading_whitespace}{new_filename}{trailing_whitespace}{line_ending}"
            )
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        return False

    with open(mod_options_order_path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(new_lines))
    return True


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


def build_garbage_collect_plan() -> list[Mod]:
    """Return disabled local mod archives that can be safely removed."""
    blacklisted_filenames = get_blacklisted_mod_filenames()
    return sorted(
        (
            mod
            for mod in get_installed_mods()
            if not _is_mod_enabled(mod, blacklisted_filenames)
        ),
        key=lambda mod: (mod.name.lower(), mod.get_filename().lower()),
    )


def garbage_collect_mods(mods: list[Mod]) -> bool:
    """Delete planned mod archives and remove their blacklist entries."""
    try:
        filenames_to_remove = {mod.get_filename() for mod in mods}
        for mod in mods:
            if os.path.exists(mod.filepath):
                os.remove(mod.filepath)
            else:
                logger.warning(f"Mod file '{mod.filepath}' does not exist.")
        _write_blacklist_filenames(filenames_to_remove=filenames_to_remove)
        return True
    except Exception as e:
        logger.error(f"Failed to garbage collect mods: {e}")
        return False


class ApplyPlanStatus(Enum):
    READY = "ready"
    EMPTY_REQUIREMENTS = "empty_requirements"
    DUPLICATE_LOCAL_MOD = "duplicate_local_mod"
    NOT_FOUND_IN_DB = "not_found_in_db"
    DOWNLOAD_FAILED = "download_failed"
    UNEXPECTED = "unexpected"


@dataclass
class ApplyPlan:
    requested: list[str]
    already_available: list[Mod] = field(default_factory=list)
    downloaded: list[Mod] = field(default_factory=list)
    enabled_closure: list[Mod] = field(default_factory=list)
    blacklisted: list[Mod] = field(default_factory=list)
    mod_options_order: list[str] = field(default_factory=list)
    would_download: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: ApplyPlanStatus = ApplyPlanStatus.READY
    dry_run: bool = False


def parse_required_mods_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def _find_duplicate_mod_names(mods: list[Mod]) -> list[str]:
    counts: dict[str, int] = {}
    for mod in mods:
        counts[mod.name] = counts.get(mod.name, 0) + 1
    return sorted((name for name, count in counts.items() if count > 1), key=str.lower)


def _sorted_unique_mods_by_name(mods_by_name: dict[str, Mod]) -> list[Mod]:
    return sorted(mods_by_name.values(), key=lambda mod: mod.name.lower())


def _build_apply_output_sets(
    requested_names: list[str], optional: bool, plan: ApplyPlan
) -> None:
    installed_mods = get_installed_mods()
    installed_dict = {mod.name: mod for mod in installed_mods}

    enabled_by_name: dict[str, Mod] = {}
    for requested_name in requested_names:
        requested_mod = installed_dict.get(requested_name)
        if not requested_mod:
            continue
        for closure_mod in _get_mod_dependency_closure_from_installed_dict(
            requested_mod, installed_dict, optional=optional
        ):
            enabled_by_name[closure_mod.name] = closure_mod

    enabled_filenames = {mod.get_filename() for mod in enabled_by_name.values()}
    blacklisted_by_name = {
        mod.name: mod
        for mod in installed_mods
        if mod.get_filename() not in enabled_filenames
    }
    plan.enabled_closure = _sorted_unique_mods_by_name(enabled_by_name)
    plan.blacklisted = _sorted_unique_mods_by_name(blacklisted_by_name)


def _build_mod_options_order_entries(
    requested_names: list[str], installed_dict: dict[str, Mod]
) -> list[str]:
    entries = []
    seen = set()
    for requested_name in requested_names:
        if requested_name in seen:
            continue
        seen.add(requested_name)
        if requested_name == "Everest":
            entries.append("Everest")
            continue
        requested_mod = installed_dict.get(requested_name)
        if requested_mod:
            entries.append(requested_mod.get_filename())
    return entries


def build_apply_plan(
    required_names: list[str], optional: bool = False, dry_run: bool = False
) -> ApplyPlan:
    requested_names = list(dict.fromkeys(required_names))
    requested_mod_names = [name for name in requested_names if name != "Everest"]
    plan = ApplyPlan(requested=requested_names, dry_run=dry_run)
    if not requested_names:
        plan.status = ApplyPlanStatus.EMPTY_REQUIREMENTS
        return plan

    try:
        installed_mods = get_installed_mods()
        duplicate_names = _find_duplicate_mod_names(installed_mods)
        if duplicate_names:
            plan.failed = duplicate_names
            plan.status = ApplyPlanStatus.DUPLICATE_LOCAL_MOD
            return plan

        installed_dict = {mod.name: mod for mod in installed_mods}
        already_by_name: dict[str, Mod] = {}
        downloaded_by_name: dict[str, Mod] = {}

        for requested_name in requested_mod_names:
            existing_mod = installed_dict.get(requested_name)
            if existing_mod:
                already_by_name[requested_name] = existing_mod
                continue

            mod_info = get_mod_info(requested_name)
            if not mod_info:
                plan.missing.append(requested_name)
                plan.status = ApplyPlanStatus.NOT_FOUND_IN_DB
                continue

            if dry_run:
                plan.would_download.append(requested_name)
                continue

            downloaded_mod, ensure_status = ensure_mod(requested_name, root=False)
            if not downloaded_mod:
                plan.failed.append(requested_name)
                if ensure_status == EnsureModStatus.NOT_FOUND_IN_DB:
                    plan.missing.append(requested_name)
                    plan.status = ApplyPlanStatus.NOT_FOUND_IN_DB
                else:
                    plan.status = ApplyPlanStatus.DOWNLOAD_FAILED
                continue
            if ensure_status == EnsureModStatus.INSTALLED:
                downloaded_by_name[downloaded_mod.name] = downloaded_mod
            else:
                already_by_name[downloaded_mod.name] = downloaded_mod
            installed_dict[downloaded_mod.name] = downloaded_mod

        if plan.status != ApplyPlanStatus.READY:
            return plan

        root_mods = list(already_by_name.values()) + list(downloaded_by_name.values())
        for root_mod in root_mods:
            if dry_run:
                for dep in root_mod.get_mod_deps(optional=optional):
                    dep_name = dep.get("Name")
                    if (
                        not dep_name
                        or dep_name in ["Everest", "Celeste", "EverestCore"]
                        or dep_name in installed_dict
                    ):
                        continue
                    if dep_name not in plan.would_download:
                        if get_mod_info(dep_name):
                            plan.would_download.append(dep_name)
                        else:
                            plan.missing.append(dep_name)
                            plan.status = ApplyPlanStatus.NOT_FOUND_IN_DB
                if plan.status != ApplyPlanStatus.READY:
                    return plan
                continue

            resolved_deps, failed_deps = resolve_deps(root_mod, optional=optional)
            for dep_mod in resolved_deps:
                downloaded_by_name[dep_mod.name] = dep_mod
            if failed_deps:
                plan.failed.extend(failed_deps)
                plan.status = ApplyPlanStatus.DOWNLOAD_FAILED
                return plan

        plan.already_available = _sorted_unique_mods_by_name(already_by_name)
        plan.downloaded = _sorted_unique_mods_by_name(downloaded_by_name)
        installed_mods = get_installed_mods()
        installed_dict = {mod.name: mod for mod in installed_mods}
        plan.mod_options_order = _build_mod_options_order_entries(
            requested_names, installed_dict
        )
        _build_apply_output_sets(requested_mod_names, optional, plan)
        return plan
    except Exception as e:
        logger.error(f"Failed to build apply plan: {e}")
        plan.status = ApplyPlanStatus.UNEXPECTED
        return plan


def apply_required_mods(plan: ApplyPlan) -> bool:
    if plan.status != ApplyPlanStatus.READY:
        return False
    try:
        _replace_blacklist_filenames({mod.get_filename() for mod in plan.blacklisted})
        _replace_mod_options_order_entries(plan.mod_options_order)
        return True
    except Exception as e:
        logger.error(f"Failed to apply required mods: {e}")
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
                if root:
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
    root_mods = get_root_mods()
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
        if updated_mod.get_filename() != mod.get_filename():
            try:
                _replace_mod_options_order_filename(
                    mod.get_filename(), updated_mod.get_filename()
                )
            except Exception as e:
                print(
                    f"WARNING: failed to update mod options order from "
                    f"'{mod.get_filename()}' to '{updated_mod.get_filename()}': {e}.",
                    file=sys.stderr,
                )
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
