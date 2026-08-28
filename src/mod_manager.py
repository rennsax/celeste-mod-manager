import http.client
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum

import xxhash
from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn

from . import mod_source
from .mod import Mod
from .mod_source import ModInfo, get_mod_info
from .operation import IssueKind, IssueSeverity, OperationIssue, has_errors
from .path import get_mods_dir

_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_PROGRESS_WIDTH = 30
_XXHASH64_HEXDIGEST_LENGTH = 16
_XXHASH_READ_CHUNK_SIZE = 1024 * 1024


@dataclass
class DownloadResult:
    mod: Mod | None = None
    issues: list[OperationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.mod is not None and not has_errors(self.issues)


@dataclass
class LocalModScanResult:
    mods: list[Mod] = field(default_factory=list)
    issues: list[OperationIssue] = field(default_factory=list)

    @property
    def invalid_filenames(self) -> set[str]:
        return {
            issue.subject
            for issue in self.issues
            if issue.kind == IssueKind.LOCAL_MOD_INVALID
        }


def _normalize_xxhash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if len(normalized) != _XXHASH64_HEXDIGEST_LENGTH:
        return None
    if any(character not in "0123456789abcdef" for character in normalized):
        return None
    return normalized


def _get_valid_mod_xxhashes(mod_info: ModInfo) -> list[str]:
    values = getattr(mod_info, "xxhashes", None)
    if not isinstance(values, (list, tuple)):
        return []
    return [
        normalized
        for value in values
        if (normalized := _normalize_xxhash(value)) is not None
    ]


def _get_expected_download_xxhash(mod_info: ModInfo) -> str | None:
    values = getattr(mod_info, "xxhashes", None)
    if not isinstance(values, (list, tuple)) or not values:
        return None
    return _normalize_xxhash(values[0])


def _calculate_xxhash64(filepath: str) -> str:
    checksum = xxhash.xxh64()
    with open(filepath, "rb") as f:
        while chunk := f.read(_XXHASH_READ_CHUNK_SIZE):
            checksum.update(chunk)
    return checksum.hexdigest().lower()


def _missing_expected_xxhash_issue(mod_name: str) -> OperationIssue:
    return OperationIssue(
        severity=IssueSeverity.ERROR,
        kind=IssueKind.CHECKSUM_FAILED,
        operation="checksum validation",
        subject=mod_name,
        detail=(
            f"cannot verify mod '{mod_name}' because the local database does not "
            "contain a valid expected xxHash"
        ),
        hint="Run 'celeste-mod-manager update-db' and retry.",
    )


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


def _retrieve_download(
    request: urllib.request.Request,
    filepath: str,
    reporthook,
) -> None:
    with urllib.request.urlopen(request) as response, open(filepath, "wb") as output:
        headers = response.info()
        content_length = headers.get("Content-Length")
        total_size = int(content_length) if content_length is not None else -1
        block_size = 1024 * 8
        block_count = 0
        bytes_read = 0
        reporthook(block_count, block_size, total_size)
        while block := response.read(block_size):
            output.write(block)
            block_count += 1
            bytes_read += len(block)
            reporthook(block_count, block_size, total_size)
    if total_size >= 0 and bytes_read < total_size:
        raise urllib.error.ContentTooShortError(
            f"retrieval incomplete: got only {bytes_read} out of {total_size} bytes",
            (filepath, headers),
        )


def _download_mod(mod_info: ModInfo) -> DownloadResult:
    if not isinstance(mod_info, ModInfo):
        return DownloadResult(
            issues=[
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.UNEXPECTED,
                    operation="download",
                    subject=getattr(mod_info, "name", "unknown"),
                    detail="invalid mod information provided for download",
                )
            ]
        )

    expected_xxhash = _get_expected_download_xxhash(mod_info)
    if expected_xxhash is None:
        return DownloadResult(issues=[_missing_expected_xxhash_issue(mod_info.name)])

    try:
        request = mod_source.get_download_request(mod_info)
    except Exception as e:
        return DownloadResult(
            issues=[
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.UNEXPECTED,
                    operation="build download request",
                    subject=mod_info.name,
                    detail=str(e),
                )
            ]
        )
    requested_filename = f"{mod_info.name}-{mod_info.version}.zip"
    mods_dir = get_mods_dir()

    expected_size = mod_info.size
    print(f"Collecting {mod_info.name}")
    print(f"  Downloading {requested_filename} ({_format_size(expected_size)})")
    logger.debug(
        f"Downloading '{mod_info.name}' from '{request.full_url}' "
        f"using the {mod_info.source.value} source..."
    )
    last_error: Exception | None = None
    retryable_errors = (
        urllib.error.URLError,
        urllib.error.ContentTooShortError,
        http.client.IncompleteRead,
        http.client.RemoteDisconnected,
        TimeoutError,
        ConnectionError,
    )

    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        temporary_filepath: str | None = None

        try:
            try:
                fd, temporary_filepath = tempfile.mkstemp(
                    prefix=f".{requested_filename}.",
                    suffix=".download.zip",
                    dir=mods_dir,
                )
            except OSError as e:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.FILESYSTEM_ERROR,
                            operation="create temporary download",
                            subject=mod_info.name,
                            detail=str(e),
                        )
                    ]
                )
            try:
                os.close(fd)
            except OSError as e:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.FILESYSTEM_ERROR,
                            operation="create temporary download",
                            subject=mod_info.name,
                            detail=str(e),
                        )
                    ]
                )
            temporary_filename = os.path.basename(temporary_filepath)
            try:
                with _download_progress(expected_size) as reporthook:
                    _retrieve_download(
                        request,
                        temporary_filepath,
                        reporthook,
                    )
            except retryable_errors as e:
                last_error = e
                logger.warning(
                    f"Download attempt {attempt}/{_DOWNLOAD_MAX_ATTEMPTS} "
                    f"for '{requested_filename}' failed: {e}"
                )
                continue
            except OSError as e:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.FILESYSTEM_ERROR,
                            operation="write temporary download",
                            subject=mod_info.name,
                            detail=str(e),
                        )
                    ]
                )
            except Exception as e:
                logger.opt(exception=e).debug(
                    f"Unexpected failure while downloading '{mod_info.name}'."
                )
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.UNEXPECTED,
                            operation="download",
                            subject=mod_info.name,
                            detail=str(e),
                        )
                    ]
                )

            try:
                actual_xxhash = _calculate_xxhash64(temporary_filepath)
            except OSError as e:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.FILESYSTEM_ERROR,
                            operation="read downloaded archive",
                            subject=mod_info.name,
                            detail=str(e),
                        )
                    ]
                )
            if actual_xxhash != expected_xxhash:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.CHECKSUM_FAILED,
                            operation="checksum validation",
                            subject=mod_info.name,
                            detail=(
                                f"file integrity check failed for mod '{mod_info.name}': "
                                f"expected xxHash '{expected_xxhash}', got '{actual_xxhash}'"
                            ),
                            hint="Run 'celeste-mod-manager update-db' and retry.",
                        )
                    ]
                )

            load_result = Mod.load_from_filename(temporary_filename)
            mod = load_result.mod
            if mod is None:
                detail = (
                    load_result.issues[0].detail
                    if load_result.issues
                    else "not a valid mod archive"
                )
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.ARCHIVE_INVALID,
                            operation="archive validation",
                            subject=mod_info.name,
                            detail=f"downloaded archive is invalid: {detail}",
                        )
                    ]
                )
            if mod.name != mod_info.name:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.ARCHIVE_INVALID,
                            operation="archive validation",
                            subject=mod_info.name,
                            detail=(
                                f"downloaded mod name mismatch: requested '{mod_info.name}', "
                                f"archive contains '{mod.name}'"
                            ),
                        )
                    ]
                )

            filename = f"{mod.name}-{mod.version}.zip"
            if "/" in filename or "\\" in filename:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.ARCHIVE_INVALID,
                            operation="archive validation",
                            subject=mod_info.name,
                            detail=(
                                "downloaded mod metadata produces an unsafe filename: "
                                f"'{filename}'"
                            ),
                        )
                    ]
                )
            filepath = str(mods_dir / filename)

            try:
                os.replace(temporary_filepath, filepath)
            except OSError as e:
                return DownloadResult(
                    issues=[
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.FILESYSTEM_ERROR,
                            operation="publish downloaded archive",
                            subject=mod_info.name,
                            detail=str(e),
                        )
                    ]
                )
            mod.filepath = filepath
            print(f"  Saved {filename}")
            logger.debug(f"Downloaded '{filename}' successfully.")
            issues = []
            if mod.version != mod_info.version:
                issues.append(
                    OperationIssue(
                        severity=IssueSeverity.WARNING,
                        kind=IssueKind.DATABASE_VERSION_MISMATCH,
                        operation="archive validation",
                        subject=mod.name,
                        detail=(
                            f"version {mod.version}, but the local database "
                            f"reports version {mod_info.version}; saved as '{filename}'"
                        ),
                        hint=(
                            "Run 'celeste-mod-manager update-db' to refresh the local "
                            "mod database."
                        ),
                    )
                )
            return DownloadResult(mod=mod, issues=issues)
        finally:
            if temporary_filepath and os.path.exists(temporary_filepath):
                try:
                    os.remove(temporary_filepath)
                except OSError as e:
                    logger.warning(
                        f"Failed to remove temporary download "
                        f"'{temporary_filepath}': {e}"
                    )

    return DownloadResult(
        issues=[
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.DOWNLOAD_FAILED,
                operation="download",
                subject=mod_info.name,
                detail=str(last_error) if last_error is not None else "unknown error",
                attempts=_DOWNLOAD_MAX_ATTEMPTS,
                retryable=True,
            )
        ]
    )


def scan_installed_mods() -> LocalModScanResult:
    mods_dir = get_mods_dir()
    if not mods_dir.exists():
        return LocalModScanResult()
    try:
        files = [f for f in os.listdir(mods_dir) if f != "Cache"]
    except OSError as e:
        return LocalModScanResult(
            issues=[
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.FILESYSTEM_ERROR,
                    operation="local mod scan",
                    subject=str(mods_dir),
                    detail=str(e),
                )
            ]
        )

    mods = []
    issues = []
    for filename in files:
        filepath = str(mods_dir / filename)
        if os.path.isfile(filepath) and filename.lower().endswith(".zip"):
            load_result = Mod.load_from_filename(filename)
            issues.extend(load_result.issues)
            if load_result.mod:
                mods.append(load_result.mod)
    return LocalModScanResult(
        mods=mods,
        issues=sorted(issues, key=lambda issue: issue.sort_key()),
    )


def get_installed_mods() -> list[Mod]:
    return scan_installed_mods().mods


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


@dataclass
class DependencyResolutionResult:
    resolved: list[Mod] = field(default_factory=list)
    issues: list[OperationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_errors(self.issues)


@dataclass
class DependencyResolutionContext:
    available_by_name: dict[str, list[Mod]]
    visited: set[str] = field(default_factory=set)
    failures_by_name: dict[str, list[OperationIssue]] = field(default_factory=dict)

    @classmethod
    def from_mods(cls, mods: list[Mod]) -> "DependencyResolutionContext":
        available_by_name: dict[str, list[Mod]] = {}
        for available_mod in mods:
            available_by_name.setdefault(available_mod.name, []).append(available_mod)
        return cls(available_by_name=available_by_name)

    def add_mod(self, mod: Mod) -> None:
        self.available_by_name.setdefault(mod.name, []).append(mod)


def resolve_deps(
    mod: Mod,
    optional: bool = False,
    *,
    context: DependencyResolutionContext | None = None,
    scan_result: LocalModScanResult | None = None,
    dependency_chain: tuple[str, ...] | None = None,
) -> DependencyResolutionResult:
    issues = []
    if context is None:
        scan_result = scan_result or scan_installed_mods()
        context = DependencyResolutionContext.from_mods(scan_result.mods)
        issues.extend(scan_result.issues)

    chain = dependency_chain or (mod.name,)
    if mod.name in context.visited:
        return DependencyResolutionResult(issues=issues)
    context.visited.add(mod.name)

    try:
        deps = mod.get_mod_deps(optional=optional)
    except Exception as e:
        logger.opt(exception=e).debug(
            f"Unexpected failure while reading dependencies for '{mod.name}'."
        )
        issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.UNEXPECTED,
                operation="dependency resolution",
                subject=mod.name,
                detail=f"failed to read dependency metadata: {e}",
                dependency_chain=chain,
            )
        )
        return DependencyResolutionResult(issues=issues)

    resolved_deps = []
    for dep in deps:
        dep_name = dep.get("Name")
        dep_version = dep.get("Version")
        if not dep_name or not dep_version:
            issues.append(
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.ARCHIVE_INVALID,
                    operation="dependency resolution",
                    subject=mod.name,
                    detail="dependency entry is missing Name or Version",
                    dependency_chain=chain,
                )
            )
            continue

        dep_name = str(dep_name)
        dep_version = str(dep_version)
        if dep_name in ["Everest", "Celeste", "EverestCore"]:
            logger.debug(f"Skipping dependency '{dep_name}' as it's a core component.")
            continue
        if dep_name in context.visited:
            continue

        dep_chain = (*chain, dep_name)
        print(f"  Resolving dependency {dep_name} ({dep_version})")

        if dep_name in context.failures_by_name:
            issues.extend(
                issue.with_dependency_chain(dep_chain)
                for issue in context.failures_by_name[dep_name]
            )
            continue

        found_mods = context.available_by_name.get(dep_name, [])
        if len(found_mods) > 1:
            issue = OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.DUPLICATE_LOCAL_MOD,
                operation="dependency resolution",
                subject=dep_name,
                detail=(", ".join(sorted(mod.get_filename() for mod in found_mods))),
                dependency_chain=dep_chain,
            )
            issues.append(issue)
            context.failures_by_name[dep_name] = [issue.with_dependency_chain(())]
            continue

        if found_mods:
            dep_mod = found_mods[0]
            print(
                f"  Requirement already satisfied: {dep_mod.name} "
                f"({dep_mod.version})"
            )
        else:
            print(f"  Downloading dependency {dep_name}")
            try:
                mod_info = get_mod_info(dep_name)
            except Exception as e:
                logger.opt(exception=e).debug(
                    f"Failed to query the database for dependency '{dep_name}'."
                )
                issue = OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.DATABASE_UNAVAILABLE,
                    operation="database lookup",
                    subject=dep_name,
                    detail=str(e),
                    dependency_chain=dep_chain,
                    retryable=True,
                )
                issues.append(issue)
                context.failures_by_name[dep_name] = [issue.with_dependency_chain(())]
                continue
            if not mod_info:
                issue = OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.NOT_FOUND_IN_DB,
                    operation="database lookup",
                    subject=dep_name,
                    detail="dependency was not found in the database",
                    dependency_chain=dep_chain,
                )
                issues.append(issue)
                context.failures_by_name[dep_name] = [issue.with_dependency_chain(())]
                continue

            download_result = _download_mod(mod_info)
            issues.extend(
                issue.with_dependency_chain(dep_chain)
                for issue in download_result.issues
            )
            dep_mod = download_result.mod
            if dep_mod is None:
                context.failures_by_name[dep_name] = [
                    issue.with_dependency_chain(())
                    for issue in download_result.issues
                    if issue.severity == IssueSeverity.ERROR
                ]
                continue
            context.add_mod(dep_mod)
            resolved_deps.append(dep_mod)

        if dep_mod.version != dep_version:
            issues.append(
                OperationIssue(
                    severity=IssueSeverity.WARNING,
                    kind=IssueKind.VERSION_MISMATCH,
                    operation="dependency resolution",
                    subject=dep_name,
                    detail=f"required {dep_version}, found {dep_mod.version}",
                    dependency_chain=dep_chain,
                )
            )

        sub_result = resolve_deps(
            dep_mod,
            optional=optional,
            context=context,
            dependency_chain=dep_chain,
        )
        resolved_deps.extend(sub_result.resolved)
        issues.extend(sub_result.issues)

    return DependencyResolutionResult(resolved=resolved_deps, issues=issues)


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
):
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


def analyse_mod_deps(maxdepth: int, optional: bool = False):
    mods = get_installed_mods()
    if not mods:
        print("No mods installed.")
        return

    blacklisted_filenames = get_blacklisted_mod_filenames()
    enabled_mods = [mod for mod in mods if _is_mod_enabled(mod, blacklisted_filenames)]
    if not enabled_mods:
        print("No enabled mods.")
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
        print("No enabled mods.")
        return

    _print_dependency_tree(
        roots,
        graph,
        installed_dict,
        maxdepth,
    )


class EnsureModStatus(Enum):
    INSTALLED = "installed"
    ALREADY_EXISTS = "already_exists"
    FAILED = "failed"


@dataclass
class EnsureModResult:
    mod: Mod | None
    status: EnsureModStatus
    issues: list[OperationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.mod is not None and not has_errors(self.issues)


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
    return str(get_mods_dir() / "blacklist.txt")


def _get_update_blacklist_path() -> str:
    return str(get_mods_dir() / "updaterblacklist.txt")


def _get_mod_options_order_path() -> str:
    return str(get_mods_dir() / "modoptionsorder.txt")


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
            entry for line in f if (entry := line.strip()) and not entry.startswith("#")
        }


def _write_blacklist_filenames(
    filenames_to_add: set[str] | None = None,
    filenames_to_remove: set[str] | None = None,
) -> None:
    filenames_to_add = filenames_to_add or set()
    filenames_to_remove = filenames_to_remove or set()

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
    new_lines = list(_BLACKLIST_HEADER)
    new_lines.extend(sorted(filenames, key=str.lower))

    blacklist_path = _get_blacklist_path()
    with open(blacklist_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines).rstrip() + "\n")


def _replace_mod_options_order_entries(entries: list[str]) -> None:
    new_lines = list(_MOD_OPTIONS_ORDER_HEADER)
    new_lines.extend(entries)

    mod_options_order_path = _get_mod_options_order_path()
    with open(mod_options_order_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines).rstrip() + "\n")


def _replace_filename_entry(
    filepath: str, old_filename: str, new_filename: str
) -> bool:
    """Replace exact archive entries without rewriting the surrounding file."""
    if not os.path.exists(filepath):
        return False

    with open(filepath, "r", encoding="utf-8", newline="") as f:
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

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        f.write("".join(new_lines))
    return True


def _replace_blacklist_filename(old_filename: str, new_filename: str) -> bool:
    return _replace_filename_entry(_get_blacklist_path(), old_filename, new_filename)


def _replace_mod_options_order_filename(old_filename: str, new_filename: str) -> bool:
    return _replace_filename_entry(
        _get_mod_options_order_path(), old_filename, new_filename
    )


def _is_mod_enabled(mod: Mod, blacklisted_filenames: set[str]) -> bool:
    return mod.get_filename() not in blacklisted_filenames


def build_garbage_collect_plan() -> list[Mod]:
    """Return disabled Installed Mod archives that can be safely removed."""
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
    FAILED = "failed"


@dataclass
class ApplyPlan:
    requested: list[str]
    already_available: list[Mod] = field(default_factory=list)
    downloaded: list[Mod] = field(default_factory=list)
    enabled_closure: list[Mod] = field(default_factory=list)
    blacklisted: list[Mod] = field(default_factory=list)
    mod_options_order: list[str] = field(default_factory=list)
    would_download: list[str] = field(default_factory=list)
    issues: list[OperationIssue] = field(default_factory=list)
    preserved_blacklist_filenames: set[str] = field(default_factory=set)
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
    requested_names: list[str],
    optional: bool,
    plan: ApplyPlan,
    installed_mods: list[Mod],
) -> None:
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
    required_names: list[str],
    optional: bool = False,
    dry_run: bool = False,
    *,
    scan_result: LocalModScanResult | None = None,
) -> ApplyPlan:
    requested_names = list(dict.fromkeys(required_names))
    requested_mod_names = [name for name in requested_names if name != "Everest"]
    plan = ApplyPlan(requested=requested_names, dry_run=dry_run)
    if not requested_names:
        plan.status = ApplyPlanStatus.FAILED
        plan.issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.EMPTY_REQUIREMENTS,
                operation="apply plan",
                subject="requirements",
                detail="no mods were requested",
            )
        )
        return plan

    try:
        scan_result = scan_result or scan_installed_mods()
        plan.issues.extend(scan_result.issues)
        installed_mods = scan_result.mods
        blacklisted_filenames = get_blacklisted_mod_filenames()
        plan.preserved_blacklist_filenames = (
            scan_result.invalid_filenames & blacklisted_filenames
        )

        duplicate_names = _find_duplicate_mod_names(installed_mods)
        duplicate_name_set = set(duplicate_names)
        for duplicate_name in duplicate_names:
            filenames = sorted(
                mod.get_filename()
                for mod in installed_mods
                if mod.name == duplicate_name
            )
            plan.issues.append(
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.DUPLICATE_LOCAL_MOD,
                    operation="apply plan",
                    subject=duplicate_name,
                    detail=(", ".join(filenames)),
                )
            )

        installed_dict = {
            mod.name: mod
            for mod in installed_mods
            if mod.name not in duplicate_name_set
        }
        already_by_name: dict[str, Mod] = {}
        downloaded_by_name: dict[str, Mod] = {}

        for requested_name in requested_mod_names:
            if requested_name in duplicate_name_set:
                continue
            existing_mod = installed_dict.get(requested_name)
            if existing_mod:
                already_by_name[requested_name] = existing_mod
                continue

            try:
                mod_info = get_mod_info(requested_name)
            except Exception as e:
                logger.opt(exception=e).debug(
                    f"Failed to query the database for mod '{requested_name}'."
                )
                plan.issues.append(
                    OperationIssue(
                        severity=IssueSeverity.ERROR,
                        kind=IssueKind.DATABASE_UNAVAILABLE,
                        operation="database lookup",
                        subject=requested_name,
                        detail=str(e),
                        retryable=True,
                    )
                )
                continue
            if not mod_info:
                plan.issues.append(
                    OperationIssue(
                        severity=IssueSeverity.ERROR,
                        kind=IssueKind.NOT_FOUND_IN_DB,
                        operation="database lookup",
                        subject=requested_name,
                        detail="mod was not found in the database",
                    )
                )
                continue

            if dry_run:
                plan.would_download.append(requested_name)
                continue

            ensure_result = ensure_mod(
                requested_name,
                scan_result=LocalModScanResult(scan_result.mods),
                mod_info=mod_info,
            )
            plan.issues.extend(ensure_result.issues)
            downloaded_mod = ensure_result.mod
            if downloaded_mod is None:
                continue
            if ensure_result.status == EnsureModStatus.INSTALLED:
                downloaded_by_name[downloaded_mod.name] = downloaded_mod
                plan.downloaded = _sorted_unique_mods_by_name(downloaded_by_name)
            else:
                already_by_name[downloaded_mod.name] = downloaded_mod
            installed_dict[downloaded_mod.name] = downloaded_mod

        requested_mods = list(already_by_name.values()) + list(
            downloaded_by_name.values()
        )
        resolution_context = DependencyResolutionContext.from_mods(scan_result.mods)
        for requested_mod in requested_mods:
            if dry_run:
                for dep in requested_mod.get_mod_deps(optional=optional):
                    dep_name = dep.get("Name")
                    if (
                        not dep_name
                        or dep_name in ["Everest", "Celeste", "EverestCore"]
                        or dep_name in installed_dict
                    ):
                        continue
                    if dep_name not in plan.would_download:
                        try:
                            dep_info = get_mod_info(dep_name)
                        except Exception as e:
                            logger.opt(exception=e).debug(
                                f"Failed to query the database for dependency '{dep_name}'."
                            )
                            plan.issues.append(
                                OperationIssue(
                                    severity=IssueSeverity.ERROR,
                                    kind=IssueKind.DATABASE_UNAVAILABLE,
                                    operation="database lookup",
                                    subject=dep_name,
                                    detail=str(e),
                                    dependency_chain=(requested_mod.name, dep_name),
                                    retryable=True,
                                )
                            )
                            continue
                        if dep_info:
                            plan.would_download.append(dep_name)
                        else:
                            plan.issues.append(
                                OperationIssue(
                                    severity=IssueSeverity.ERROR,
                                    kind=IssueKind.NOT_FOUND_IN_DB,
                                    operation="database lookup",
                                    subject=dep_name,
                                    detail="dependency was not found in the database",
                                    dependency_chain=(requested_mod.name, dep_name),
                                )
                            )
                continue

            resolution_result = resolve_deps(
                requested_mod,
                optional=optional,
                context=resolution_context,
            )
            plan.issues.extend(resolution_result.issues)
            for dep_mod in resolution_result.resolved:
                downloaded_by_name[dep_mod.name] = dep_mod
                if dep_mod not in scan_result.mods:
                    scan_result.mods.append(dep_mod)
            plan.downloaded = _sorted_unique_mods_by_name(downloaded_by_name)

        plan.already_available = _sorted_unique_mods_by_name(already_by_name)
        plan.downloaded = _sorted_unique_mods_by_name(downloaded_by_name)
        plan.issues = sorted(set(plan.issues), key=lambda issue: issue.sort_key())
        if has_errors(plan.issues):
            plan.status = ApplyPlanStatus.FAILED
            return plan

        installed_mods = scan_result.mods
        installed_dict = {mod.name: mod for mod in installed_mods}
        plan.mod_options_order = _build_mod_options_order_entries(
            requested_names, installed_dict
        )
        _build_apply_output_sets(
            requested_mod_names,
            optional,
            plan,
            installed_mods,
        )
        return plan
    except Exception as e:
        logger.opt(exception=e).debug("Failed to build apply plan.")
        plan.issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.UNEXPECTED,
                operation="apply plan",
                subject="requirements",
                detail=str(e),
            )
        )
        plan.issues = sorted(set(plan.issues), key=lambda issue: issue.sort_key())
        plan.status = ApplyPlanStatus.FAILED
        return plan


def apply_required_mods(plan: ApplyPlan) -> bool:
    if plan.status != ApplyPlanStatus.READY:
        return False
    try:
        _replace_blacklist_filenames(
            {mod.get_filename() for mod in plan.blacklisted}
            | plan.preserved_blacklist_filenames
        )
        _replace_mod_options_order_entries(plan.mod_options_order)
        return True
    except Exception as e:
        logger.error(f"Failed to apply required mods: {e}")
        return False


_MOD_INFO_UNSET = object()


def ensure_mod(
    mod_name: str,
    *,
    scan_result: LocalModScanResult | None = None,
    mod_info: ModInfo | None | object = _MOD_INFO_UNSET,
) -> EnsureModResult:
    """Ensure that a mod is installed and retain structured failure details."""
    scan_result = scan_result or scan_installed_mods()
    issues = list(scan_result.issues)
    if has_errors(issues):
        return EnsureModResult(None, EnsureModStatus.FAILED, issues)

    found_mods = [mod for mod in scan_result.mods if mod.name == mod_name]
    if len(found_mods) > 1:
        issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.DUPLICATE_LOCAL_MOD,
                operation="install",
                subject=mod_name,
                detail=", ".join(sorted(mod.get_filename() for mod in found_mods)),
            )
        )
        return EnsureModResult(None, EnsureModStatus.FAILED, issues)
    if found_mods:
        mod = found_mods[0]
        return EnsureModResult(mod, EnsureModStatus.ALREADY_EXISTS, issues)

    if mod_info is _MOD_INFO_UNSET:
        try:
            mod_info = get_mod_info(mod_name)
        except Exception as e:
            logger.opt(exception=e).debug(
                f"Failed to query the database for mod '{mod_name}'."
            )
            issues.append(
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.DATABASE_UNAVAILABLE,
                    operation="database lookup",
                    subject=mod_name,
                    detail=str(e),
                    retryable=True,
                )
            )
            return EnsureModResult(None, EnsureModStatus.FAILED, issues)
    if not mod_info:
        issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.NOT_FOUND_IN_DB,
                operation="database lookup",
                subject=mod_name,
                detail="mod was not found in the database",
            )
        )
        return EnsureModResult(None, EnsureModStatus.FAILED, issues)

    download_result = _download_mod(mod_info)
    issues.extend(download_result.issues)
    mod = download_result.mod
    if mod is None:
        return EnsureModResult(None, EnsureModStatus.FAILED, issues)
    scan_result.mods.append(mod)
    return EnsureModResult(mod, EnsureModStatus.INSTALLED, issues)


class UpdateModStatus(Enum):
    UPDATED = "updated"
    ALREADY_UP_TO_DATE = "already_up_to_date"
    FAILED = "failed"


@dataclass
class UpdateModResult:
    mod: Mod | None
    status: UpdateModStatus
    issues: list[OperationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.mod is not None and not has_errors(self.issues)


def update_mod(mod: Mod, mod_info: ModInfo | None = None) -> UpdateModResult:
    """Update a mod and retain structured failure details."""
    issues = []
    try:
        if mod_info is None:
            try:
                mod_info = get_mod_info(mod.name)
            except Exception as e:
                logger.opt(exception=e).debug(
                    f"Failed to query the database for mod '{mod.name}'."
                )
                issues.append(
                    OperationIssue(
                        severity=IssueSeverity.ERROR,
                        kind=IssueKind.DATABASE_UNAVAILABLE,
                        operation="database lookup",
                        subject=mod.name,
                        detail=str(e),
                        retryable=True,
                    )
                )
                return UpdateModResult(None, UpdateModStatus.FAILED, issues)
        if not mod_info:
            issues.append(
                OperationIssue(
                    severity=IssueSeverity.ERROR,
                    kind=IssueKind.NOT_FOUND_IN_DB,
                    operation="database lookup",
                    subject=mod.name,
                    detail="mod was not found in the database",
                )
            )
            return UpdateModResult(None, UpdateModStatus.FAILED, issues)

        valid_xxhashes = _get_valid_mod_xxhashes(mod_info)
        if not valid_xxhashes:
            issues.append(_missing_expected_xxhash_issue(mod.name))
            return UpdateModResult(None, UpdateModStatus.FAILED, issues)

        current_xxhash = _calculate_xxhash64(mod.filepath)
        if current_xxhash in valid_xxhashes:
            return UpdateModResult(mod, UpdateModStatus.ALREADY_UP_TO_DATE, issues)

        download_result = _download_mod(mod_info)
        issues.extend(download_result.issues)
        updated_mod = download_result.mod
        if updated_mod is None:
            return UpdateModResult(None, UpdateModStatus.FAILED, issues)
        if updated_mod.get_filename() != mod.get_filename():
            try:
                _replace_blacklist_filename(
                    mod.get_filename(), updated_mod.get_filename()
                )
            except Exception as e:
                issues.append(
                    OperationIssue(
                        severity=IssueSeverity.WARNING,
                        kind=IssueKind.FILESYSTEM_ERROR,
                        operation="update blacklist",
                        subject=mod.name,
                        detail=(
                            f"failed to replace '{mod.get_filename()}' with "
                            f"'{updated_mod.get_filename()}': {e}"
                        ),
                    )
                )
            try:
                _replace_mod_options_order_filename(
                    mod.get_filename(), updated_mod.get_filename()
                )
            except Exception as e:
                issues.append(
                    OperationIssue(
                        severity=IssueSeverity.WARNING,
                        kind=IssueKind.FILESYSTEM_ERROR,
                        operation="update mod options order",
                        subject=mod.name,
                        detail=(
                            f"failed to replace '{mod.get_filename()}' with "
                            f"'{updated_mod.get_filename()}': {e}"
                        ),
                    )
                )
        if updated_mod.filepath != mod.filepath:
            os.remove(mod.filepath)
        return UpdateModResult(updated_mod, UpdateModStatus.UPDATED, issues)
    except OSError as e:
        issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.FILESYSTEM_ERROR,
                operation="update mod",
                subject=mod.name,
                detail=str(e),
            )
        )
        return UpdateModResult(None, UpdateModStatus.FAILED, issues)
    except Exception as e:
        logger.opt(exception=e).debug(f"Failed to update mod '{mod.name}'.")
        issues.append(
            OperationIssue(
                severity=IssueSeverity.ERROR,
                kind=IssueKind.UNEXPECTED,
                operation="update mod",
                subject=mod.name,
                detail=str(e),
            )
        )
        return UpdateModResult(None, UpdateModStatus.FAILED, issues)
