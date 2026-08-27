import optparse
import os
import sys
import textwrap
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from loguru import logger

from . import config, everest_manager, mod_db, mod_manager
from .operation import IssueKind, IssueSeverity, OperationIssue, has_errors
from .path import get_configured_celeste_dir, get_mods_dir


class _UpdateCheckStatus(Enum):
    BLACKLISTED = "blacklisted"
    UNKNOWN = "unknown"
    REMOTE_HASH_UNAVAILABLE = "remote_hash_unavailable"
    LOCAL_HASH_UNAVAILABLE = "local_hash_unavailable"
    OUTDATED = "outdated"
    UP_TO_DATE = "up_to_date"


@dataclass(frozen=True)
class _UpdateCheckEntry:
    mod: mod_manager.Mod
    status: _UpdateCheckStatus
    mod_info: mod_db.ModInfo | None = None
    error: OSError | None = None


class CelesteModCLI:

    @staticmethod
    def _show_everest_help(prog_name: str) -> None:
        help_text = f"""\
                Usage:
                  {prog_name}
                    Interactively install, update, reinstall, or downgrade Everest.

                  {prog_name} -h | --help
                    Show this help message.

                Options:
                  -h, --help  Show this help message.

                Only modern native Everest builds are available. The command downloads
                an official main.zip and runs the platform MiniInstaller. Celeste is not
                started automatically after installation."""
        print(textwrap.dedent(help_text))

    @staticmethod
    def _everest_build_label(build: everest_manager.EverestBuild) -> str:
        version = build.version
        return (
            f"{version.version_string:<12} "
            f"{version.branch:<6} "
            f"{build.date[:10]:<10} "
            f"{mod_manager._format_size(build.main_file_size)}"
        )

    @staticmethod
    def _is_current_everest_build(
        current: everest_manager.EverestVersion | None,
        build: everest_manager.EverestBuild,
    ) -> bool:
        return current is not None and (
            current.build == build.version.build
            and current.branch == build.version.branch
        )

    def _browse_everest_builds(
        self,
        builds: list[everest_manager.EverestBuild],
        current: everest_manager.EverestVersion | None,
    ) -> tuple[everest_manager.EverestBuild | None, bool]:
        by_number = {build.version.build: build for build in builds}
        page = 0
        total_pages = everest_manager.page_count(builds)

        while True:
            print()
            print(f"All native Everest builds (page {page + 1}/{total_pages}):")
            for build in everest_manager.build_page(builds, page):
                marker = (
                    " [CURRENT]"
                    if self._is_current_everest_build(current, build)
                    else ""
                )
                print(
                    f"  [{build.version.build}] "
                    f"{self._everest_build_label(build)}{marker}"
                )
            print("Commands: n=next, p=previous, b=back, q=cancel")
            choice = input("Select a build number or command: ").strip().lower()
            if choice == "q":
                return None, True
            if choice == "b":
                return None, False
            if choice == "n":
                if page + 1 < total_pages:
                    page += 1
                else:
                    print("Already on the last page.")
                continue
            if choice == "p":
                if page > 0:
                    page -= 1
                else:
                    print("Already on the first page.")
                continue
            if choice.isdigit() and int(choice) in by_number:
                return by_number[int(choice)], False
            print("Invalid selection. Enter a listed build number or command.")

    def _select_everest_build(
        self,
        builds: list[everest_manager.EverestBuild],
        current: everest_manager.EverestVersion | None,
    ) -> everest_manager.EverestBuild | None:
        latest = everest_manager.newest_builds_by_branch(builds)
        by_number = {build.version.build: build for build in builds}

        while True:
            print()
            print("Latest native Everest builds:")
            for index, build in enumerate(latest, start=1):
                markers: list[str] = []
                if index == 1:
                    markers.append("RECOMMENDED, DEFAULT")
                if self._is_current_everest_build(current, build):
                    markers.append("CURRENT")
                suffix = f" [{', '.join(markers)}]" if markers else ""
                print(f"  [{index}] {self._everest_build_label(build)}{suffix}")
            print("  [a] Browse all native builds")
            print("  [q] Cancel")

            choice = input("Select a version [1]: ").strip().lower()
            if choice == "":
                return latest[0]
            if choice == "q":
                return None
            if choice == "a":
                selected, cancelled = self._browse_everest_builds(builds, current)
                if cancelled:
                    return None
                if selected is not None:
                    return selected
                continue
            if choice.isdigit():
                number = int(choice)
                if 1 <= number <= len(latest):
                    return latest[number - 1]
                if number in by_number:
                    return by_number[number]
            print("Invalid selection. Choose a listed option or Everest build number.")

    @staticmethod
    def _describe_everest_state(
        state: everest_manager.EverestInstallationState,
    ) -> str:
        if state.kind == everest_manager.EverestStateKind.VANILLA:
            return "Vanilla Celeste (Everest is not installed)"
        if state.version is None:
            return "Unknown"
        return f"{state.version.version_string} ({state.version.branch})"

    @staticmethod
    def _report_everest_state(
        celeste_dir: Path,
        state: everest_manager.EverestInstallationState,
    ) -> None:
        print("Current Everest installation:")
        print(f"  Celeste directory: {celeste_dir}")
        if state.kind == everest_manager.EverestStateKind.VANILLA:
            print("  Status:            Not installed (vanilla Celeste)")
        elif state.version is not None:
            print("  Status:            Installed")
            print(f"  Version:           {state.version.version_string}")
            print(f"  Channel:           {state.version.branch}")
        else:
            print("  Status:            Unknown")

    @staticmethod
    def _confirm(prompt: str) -> bool:
        return input(prompt).strip().lower() in ("y", "yes")

    def everest(
        self,
        args: Sequence[str],
        prog_name: str = "celeste-mod-manager everest",
    ) -> int:
        """Interactively install or update Everest using MiniInstaller."""
        if list(args) in (["-h"], ["--help"]):
            self._show_everest_help(prog_name)
            return 0
        if args:
            print(
                f"ERROR: unexpected argument(s): {' '.join(args)}. "
                "The everest command only supports -h and --help.",
                file=sys.stderr,
            )
            return 1
        if not sys.stdin.isatty():
            print(
                "ERROR: the everest command requires an interactive terminal.",
                file=sys.stderr,
            )
            return 1

        celeste_dir = get_configured_celeste_dir()
        try:
            state = everest_manager.detect_installation(celeste_dir)
            self._report_everest_state(celeste_dir, state)
            everest_manager.ensure_supported_platform()
            if state.kind == everest_manager.EverestStateKind.UNKNOWN:
                print(
                    "WARNING: could not reliably detect the current Celeste/Everest "
                    f"state: {state.detail}",
                    file=sys.stderr,
                )
                if not self._confirm("Continue despite the detection failure? [y/N] "):
                    print("Cancelled Everest installation.")
                    return 0

            builds = everest_manager.get_available_builds()
            selected = self._select_everest_build(builds, state.version)
            if selected is None:
                print("Cancelled Everest installation.")
                return 0

            action = everest_manager.classify_action(state, selected)
            if action == everest_manager.EverestAction.REINSTALL:
                print(
                    "WARNING: the selected build is already installed. Reinstalling "
                    "will overwrite Everest runtime files."
                )
                if not self._confirm("Continue with the reinstall? [y/N] "):
                    print("Cancelled Everest installation.")
                    return 0
            elif action == everest_manager.EverestAction.DOWNGRADE:
                current = state.version
                assert current is not None
                print(
                    "WARNING: downgrading Everest can make newer mods or settings "
                    f"incompatible ({current.version_string} -> "
                    f"{selected.version.version_string})."
                )
                if not self._confirm("Continue with the downgrade? [y/N] "):
                    print("Cancelled Everest installation.")
                    return 0

            delete_stale_orig = (
                state.kind == everest_manager.EverestStateKind.VANILLA
                and (celeste_dir / "orig").is_dir()
            )
            print()
            print("Everest installation summary:")
            print(f"  Celeste directory: {celeste_dir}")
            print(f"  Current version:   {self._describe_everest_state(state)}")
            print(
                f"  Target version:    {selected.version.version_string} "
                f"(build {selected.version.build})"
            )
            print(f"  Channel:           {selected.version.branch}")
            print(
                f"  Download size:     "
                f"{mod_manager._format_size(selected.main_file_size)}"
            )
            print(f"  Action:            {action.value}")
            if delete_stale_orig:
                print(
                    "  Existing orig/:    Will be deleted so MiniInstaller can "
                    "create a fresh backup"
                )

            if not self._confirm("Proceed? [y/N] "):
                print("Cancelled Everest installation.")
                return 0

            everest_manager.install_build(
                celeste_dir,
                selected,
                delete_stale_orig=delete_stale_orig,
            )
        except EOFError:
            print("Cancelled Everest installation.")
            return 0
        except everest_manager.EverestError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        print(
            f"Successfully installed Everest {selected.version.version_string} "
            f"({selected.version.branch})."
        )
        print("Celeste was not started automatically.")
        return 0

    def _render_issues(self, issues: Sequence[OperationIssue]) -> None:
        unique_issues = sorted(
            set(issues),
            key=lambda issue: (
                0 if issue.severity == IssueSeverity.WARNING else 1,
                issue.sort_key(),
            ),
        )
        for issue in unique_issues:
            prefix = "WARNING" if issue.severity == IssueSeverity.WARNING else "ERROR"
            detail = issue.detail.rstrip(".")
            chain_suffix = ""
            if len(issue.dependency_chain) > 1:
                chain_suffix = (
                    " Dependency chain: " + " -> ".join(issue.dependency_chain) + "."
                )

            if issue.kind == IssueKind.LOCAL_MOD_INVALID:
                message = f"skipped local ZIP '{issue.subject}': {detail}."
            elif issue.kind == IssueKind.DOWNLOAD_FAILED:
                attempts = issue.attempts or 1
                message = (
                    f"failed to download mod '{issue.subject}' after {attempts} "
                    f"attempt{'s' if attempts != 1 else ''}: {detail}."
                )
            elif issue.kind == IssueKind.DATABASE_UNAVAILABLE:
                message = (
                    f"failed to query the mod database for '{issue.subject}': "
                    f"{detail}.{chain_suffix}"
                )
            elif issue.kind == IssueKind.NOT_FOUND_IN_DB:
                noun = "dependency" if len(issue.dependency_chain) > 1 else "mod"
                message = (
                    f"{noun} '{issue.subject}' was not found in the database."
                    f"{chain_suffix}"
                )
            elif issue.kind == IssueKind.CHECKSUM_FAILED:
                message = f"{detail}."
            elif issue.kind == IssueKind.ARCHIVE_INVALID:
                message = (
                    f"failed to validate archive for mod '{issue.subject}': "
                    f"{detail}.{chain_suffix}"
                )
            elif issue.kind == IssueKind.DUPLICATE_LOCAL_MOD:
                message = (
                    f"multiple local archives found for mod '{issue.subject}': "
                    f"{detail}.{chain_suffix}"
                )
            elif issue.kind == IssueKind.VERSION_MISMATCH:
                message = (
                    f"dependency version mismatch for '{issue.subject}': "
                    f"{detail}.{chain_suffix}"
                )
            elif issue.kind == IssueKind.DATABASE_VERSION_MISMATCH:
                message = f"downloaded '{issue.subject}' {detail}."
            elif issue.kind == IssueKind.EMPTY_REQUIREMENTS:
                message = "no mods were requested."
            elif issue.kind == IssueKind.FILESYSTEM_ERROR:
                message = (
                    f"{issue.operation} failed for '{issue.subject}': {detail}."
                    f"{chain_suffix}"
                )
            else:
                message = (
                    f"unexpected error during {issue.operation} for "
                    f"'{issue.subject}': {detail}.{chain_suffix}"
                )

            if issue.hint:
                message += f" {issue.hint}"
            print(f"{prefix}: {message}", file=sys.stderr)

    def _scan_installed_mods(self) -> mod_manager.LocalModScanResult:
        scan_result = mod_manager.scan_installed_mods()
        self._render_issues(scan_result.issues)
        return scan_result

    def _load_update_mod_index(self) -> dict[str, mod_db.ModInfo] | None:
        url = f"{config.WEGFAN_API_URL}/mod/list"
        try:
            mod_list = mod_db.get_mod_db(url, force_update=True)
        except Exception as refresh_error:
            print(
                "WARNING: failed to refresh the local mod database: "
                f"{refresh_error}. Using the existing cached database.",
                file=sys.stderr,
            )
            try:
                mod_list = mod_db.get_cached_mod_db()
            except Exception as cache_error:
                print(
                    "ERROR: failed to load the local mod database cache: "
                    f"{cache_error}",
                    file=sys.stderr,
                )
                return None

        try:
            return mod_db.index_mod_infos(mod_list)
        except Exception as e:
            print(
                f"ERROR: failed to parse the local mod database: {e}",
                file=sys.stderr,
            )
            return None

    def _collect_update_check_entries(self) -> list[_UpdateCheckEntry] | None:
        scan_result = self._scan_installed_mods()
        if has_errors(scan_result.issues):
            return None
        mods = sorted(scan_result.mods, key=lambda mod: mod.name.lower())
        if not mods:
            return []

        try:
            update_blacklisted_filenames = (
                mod_manager.get_update_blacklisted_mod_filenames()
            )
        except OSError as e:
            print(f"ERROR: failed to read update blacklist: {e}", file=sys.stderr)
            return None

        mod_info_index = self._load_update_mod_index()
        if mod_info_index is None:
            return None

        entries: list[_UpdateCheckEntry] = []
        for mod in mods:
            if mod.get_filename() in update_blacklisted_filenames:
                entries.append(_UpdateCheckEntry(mod, _UpdateCheckStatus.BLACKLISTED))
                continue

            cur_mod_info = mod_info_index.get(mod.name)
            if cur_mod_info is None:
                entries.append(_UpdateCheckEntry(mod, _UpdateCheckStatus.UNKNOWN))
                continue

            valid_xxhashes = mod_manager._get_valid_mod_xxhashes(cur_mod_info)
            if not valid_xxhashes:
                entries.append(
                    _UpdateCheckEntry(
                        mod,
                        _UpdateCheckStatus.REMOTE_HASH_UNAVAILABLE,
                        cur_mod_info,
                    )
                )
                continue

            try:
                local_xxhash = mod_manager._calculate_xxhash64(mod.filepath)
            except OSError as e:
                entries.append(
                    _UpdateCheckEntry(
                        mod,
                        _UpdateCheckStatus.LOCAL_HASH_UNAVAILABLE,
                        cur_mod_info,
                        e,
                    )
                )
                continue

            status = (
                _UpdateCheckStatus.OUTDATED
                if local_xxhash not in valid_xxhashes
                else _UpdateCheckStatus.UP_TO_DATE
            )
            entries.append(_UpdateCheckEntry(mod, status, cur_mod_info))

        return entries

    def search(self, args: list[str]) -> int:
        """Search for mods in the database and print their information."""
        if not args:
            print("ERROR: no search pattern specified.", file=sys.stderr)
            return 1

        pattern = args[0]
        try:
            found_mods = mod_db.search_mod_by_name(pattern)
        except Exception as e:
            logger.opt(exception=e).debug("Failed to load the local mod database.")
            print(
                f"ERROR: failed to load the local mod database: {e}",
                file=sys.stderr,
            )
            return 1
        if not found_mods:
            print(f"No mods found.")
            return 0

        print(f"Found {len(found_mods)} mod(s) :")
        print("-" * 40)

        for mod in found_mods:
            mod_db.pretty_print_mod_info(mod)
            print("-" * 40)
        return 0

    def list_mods(
        self, args: list[str], prog_name: str = "celeste-mod-manager list"
    ) -> int:
        """List installed mods."""
        parser = optparse.OptionParser(prog=prog_name)
        parser.add_option(
            "--enabled",
            action="store_true",
            dest="enabled_only",
            default=False,
            help="Only list enabled mods.",
        )
        options, _ = parser.parse_args(args)
        scan_result = self._scan_installed_mods()
        if has_errors(scan_result.issues):
            return 1
        mods = scan_result.mods
        if options.enabled_only:
            blacklisted_filenames = mod_manager.get_blacklisted_mod_filenames()
            mods = [
                mod for mod in mods if mod.get_filename() not in blacklisted_filenames
            ]
        mod_manager.pretty_print_mods(mods, show_enabled=not options.enabled_only)
        return 0

    def list_tree(
        self, args: list[str], prog_name: str = "celeste-mod-manager list-tree"
    ) -> int:
        """List all installed mods and their dependencies in a tree format."""
        parser = optparse.OptionParser(
            prog=prog_name,
        )
        parser.add_option(
            "--maxdepth",
            "-d",
            dest="max_depth",
            type="int",
            default=2,
            help="Maximum depth of the dependency tree to display. (Default: 2)",
        )
        parser.add_option(
            "--optional-deps",
            action="store_true",
            dest="optional_deps",
            default=False,
            help="Also include optional dependencies in the tree.",
        )
        options, _ = parser.parse_args(args)
        if options.max_depth <= 0:
            print("ERROR: max depth must be a positive integer.", file=sys.stderr)
            return 1
        scan_result = self._scan_installed_mods()
        if has_errors(scan_result.issues):
            return 1
        mod_manager.analyse_mod_deps(
            maxdepth=options.max_depth,
            optional=options.optional_deps,
        )
        return 0

    def check_updates(self, args: list[str]) -> int:
        """Check for updates for all installed mods."""
        entries = self._collect_update_check_entries()
        if entries is None:
            return 1
        if not entries:
            print("No mods installed.")
            return 0

        name_width = max(len(entry.mod.name) for entry in entries)
        status_width = max(len("[OUTDATED]"), len("[BLACKLISTED]"))
        up_to_date_count = 0
        update_available_count = 0
        skipped_count = 0
        blacklisted_count = 0
        version_warnings: list[tuple[mod_manager.Mod, mod_db.ModInfo]] = []

        print("-" * 72)
        print(f"{'Status':<{status_width}}  {'Mod':<{name_width}}  Version")
        print("-" * 72)

        for entry in entries:
            mod = entry.mod
            if entry.status == _UpdateCheckStatus.BLACKLISTED:
                print(
                    f"{'[BLACKLISTED]':<{status_width}}  {mod.name:<{name_width}}  "
                    f"local={mod.version}  remote=not checked"
                )
                blacklisted_count += 1
                continue

            if entry.status == _UpdateCheckStatus.UNKNOWN:
                print(
                    f"{'[SKIP]':<{status_width}}  {mod.name:<{name_width}}  local={mod.version}  remote=unknown"
                )
                skipped_count += 1
                continue

            if entry.status == _UpdateCheckStatus.REMOTE_HASH_UNAVAILABLE:
                print(
                    f"{'[SKIP]':<{status_width}}  {mod.name:<{name_width}}  "
                    f"local={mod.version}  remote hash unavailable"
                )
                skipped_count += 1
                continue

            if entry.status == _UpdateCheckStatus.LOCAL_HASH_UNAVAILABLE:
                print(
                    f"{'[SKIP]':<{status_width}}  {mod.name:<{name_width}}  "
                    f"local={mod.version}  local hash unavailable"
                )
                print(
                    f"WARNING: failed to calculate xxHash for mod '{mod.name}': "
                    f"{entry.error}",
                    file=sys.stderr,
                )
                skipped_count += 1
                continue

            cur_mod_info = entry.mod_info
            assert cur_mod_info is not None
            if entry.status == _UpdateCheckStatus.OUTDATED:
                print(
                    f"\033[93m{'[OUTDATED]':<{status_width}}\033[0m  {mod.name:<{name_width}}  {mod.version} -> {cur_mod_info.version}"
                )
                update_available_count += 1
            elif entry.status == _UpdateCheckStatus.UP_TO_DATE:
                print(
                    f"\033[92m{'[OK]':<{status_width}}\033[0m  {mod.name:<{name_width}}  {mod.version}"
                )
                up_to_date_count += 1
                if mod.version != cur_mod_info.version:
                    version_warnings.append((mod, cur_mod_info))

        for mod, cur_mod_info in version_warnings:
            print(
                f"\033[93m{'[WARNING]':<{status_width}}\033[0m  "
                f"{mod.name:<{name_width}}  local={mod.version}  "
                f"database={cur_mod_info.version}; xxHash matches, treated as up to date"
            )

        print("-" * 72)
        print(
            f"Summary: total={len(entries)}, outdated={update_available_count}, "
            f"up-to-date={up_to_date_count}, skipped={skipped_count}, "
            f"blacklisted={blacklisted_count}"
        )
        return 0

    def _get_installed_mod_by_name(
        self, mod_name: str, mods: Sequence[mod_manager.Mod] | None = None
    ) -> mod_manager.Mod | None:
        """Get an installed mod by its name. Return None if not found."""
        mods = mods if mods is not None else mod_manager.get_installed_mods()
        for mod in mods:
            if mod.name == mod_name:
                return mod
        return None

    def update_db(self, args: list[str]) -> int:
        """Force update the local mod database from the server."""
        try:
            _ = mod_db.get_mod_db(
                f"{config.WEGFAN_API_URL}/mod/list", force_update=True
            )
            print("Successfully updated the local mod database.")
            return 0
        except Exception as e:
            print(
                f"ERROR: failed to update the local mod database: {e}", file=sys.stderr
            )
            return 1

    def apply(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager apply"
    ) -> int:
        """Apply a requirement file as the desired mod state."""

        def show_help() -> None:
            print(
                textwrap.dedent(
                    f"""\
                Usage:
                  {prog_name} [options]
                    Apply Mods/required_mods.txt declaratively.

                  {prog_name} [options] -r FILE
                    Apply mods declaratively from FILE.

                  {prog_name} --help | -h
                    Show this help message.

                Options:
                  -r, --requirement FILE  Requirement file to apply.
                  --dry-run               Show the planned target state without downloading
                                          or writing any configuration files.
                  --optional-deps         Also include optional dependencies when resolving dependencies.

                The apply command rewrites blacklist.txt and modoptionsorder.txt from the requested mods plus their required dependencies."""
                )
            )

        parser = optparse.OptionParser(
            prog=prog_name,
            add_help_option=False,
            usage="",
        )
        parser.add_option("-r", "--requirement", dest="requirement", metavar="FILE")
        parser.add_option(
            "--dry-run", action="store_true", dest="dry_run", default=False
        )
        parser.add_option(
            "--optional-deps",
            action="store_true",
            dest="optional_deps",
            default=False,
        )
        parser.add_option("-h", "--help", action="store_true", dest="help")

        options, positionals = parser.parse_args(list(args))
        if options.help:
            show_help()
            return 0
        if positionals:
            print(
                f"ERROR: unexpected argument(s): {' '.join(positionals)}",
                file=sys.stderr,
            )
            return 1

        requirement_path = options.requirement or str(
            get_mods_dir() / "required_mods.txt"
        )
        if not os.path.isfile(requirement_path):
            print(
                f"ERROR: requirement file '{requirement_path}' not found.",
                file=sys.stderr,
            )
            return 1

        required_mods = mod_manager.parse_required_mods_file(requirement_path)
        if not required_mods:
            print(
                f"ERROR: requirement file '{requirement_path}' does not declare any mods.",
                file=sys.stderr,
            )
            return 1

        print(f"Applying declarative mod state from {requirement_path}")
        print(f"Requested mods: {len(required_mods)}")
        if options.dry_run:
            print(
                "Dry run: no downloads, blacklist changes, or mod options order changes will be made."
            )

        plan = mod_manager.build_apply_plan(
            required_mods,
            optional=options.optional_deps,
            dry_run=options.dry_run,
        )
        self._render_issues(plan.issues)
        if plan.status == mod_manager.ApplyPlanStatus.FAILED:
            if plan.downloaded:
                print(
                    "WARNING: apply did not update generated state files; the "
                    "following verified downloads remain installed but were not enabled:",
                    file=sys.stderr,
                )
                for mod in sorted(plan.downloaded, key=lambda mod: mod.name.casefold()):
                    print(
                        f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]",
                        file=sys.stderr,
                    )
            return 1

        if not options.dry_run and not mod_manager.apply_required_mods(plan):
            print("ERROR: failed to write generated mod state files.", file=sys.stderr)
            return 1

        print(
            "Summary: "
            f"already-available={len(plan.already_available)}, "
            f"downloaded={len(plan.downloaded)}, "
            f"would-download={len(plan.would_download)}, "
            f"enabled={len(plan.enabled_closure)}, "
            f"blacklisted={len(plan.blacklisted)}, "
            f"mod-options-order={len(plan.mod_options_order)}"
        )
        if plan.downloaded:
            print("Downloaded mods:")
            for mod in plan.downloaded:
                print(f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]")
        if plan.would_download:
            print("Would download:")
            for mod_name in plan.would_download:
                print(f"  - {mod_name}")
        if plan.enabled_closure:
            print("Enabled mods:")
            for mod in plan.enabled_closure:
                print(f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]")
        if plan.blacklisted:
            print("Blacklisted mods:")
            for mod in plan.blacklisted:
                print(f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]")
        return 0

    def garbage_collect(
        self,
        args: Sequence[str],
        prog_name: str = "celeste-mod-manager garbage-collect",
    ) -> int:
        """Delete locally installed mods that are currently disabled."""
        parser = optparse.OptionParser(prog=prog_name)
        _, positionals = parser.parse_args(list(args))
        if positionals:
            print(
                f"ERROR: unexpected argument(s): {' '.join(positionals)}",
                file=sys.stderr,
            )
            return 1

        scan_result = self._scan_installed_mods()
        if has_errors(scan_result.issues):
            return 1

        mods_to_delete = mod_manager.build_garbage_collect_plan()
        if not mods_to_delete:
            print("No disabled mods to delete.")
            return 0

        print("The following disabled mod(s) will be deleted:")
        for mod in mods_to_delete:
            print(f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]")

        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Skipped deleting mods.")
            return 0

        if mod_manager.garbage_collect_mods(mods_to_delete):
            print("Successfully deleted mods.")
            return 0

        print("ERROR: failed to delete mods.", file=sys.stderr)
        return 1

    def _upgrade_mod(
        self,
        mod: mod_manager.Mod,
        mod_info: mod_db.ModInfo | None,
        display_name: str | None = None,
    ) -> int:
        mod_name = display_name if display_name is not None else mod.name
        logger.info(f"Try to update mod '{mod_name}'...")
        if mod_info is None:
            result = mod_manager.UpdateModResult(
                None,
                mod_manager.UpdateModStatus.FAILED,
                [
                    OperationIssue(
                        severity=IssueSeverity.ERROR,
                        kind=IssueKind.NOT_FOUND_IN_DB,
                        operation="database lookup",
                        subject=mod_name,
                        detail="mod was not found in the database",
                    )
                ],
            )
        else:
            result = mod_manager.update_mod(mod, mod_info=mod_info)

        self._render_issues(result.issues)
        updated_mod = result.mod
        status = result.status

        if status == mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE:
            print(f"'{mod_name}' is already up to date.")
            return 0
        if status == mod_manager.UpdateModStatus.UPDATED:
            if updated_mod is None:
                self._render_issues(
                    [
                        OperationIssue(
                            severity=IssueSeverity.ERROR,
                            kind=IssueKind.UNEXPECTED,
                            operation="update mod",
                            subject=mod_name,
                            detail="update reported success without returning a mod",
                        )
                    ]
                )
                return 1
            print(
                f"Successfully updated '{mod_name}' from v{mod.version} to "
                f"v{updated_mod.version}.\n"
            )
            return 0
        return 1

    def _upgrade_all(self) -> int:
        entries = self._collect_update_check_entries()
        if entries is None:
            return 1
        if not entries:
            print("No mods installed.")
            return 0

        for entry in entries:
            if entry.status == _UpdateCheckStatus.LOCAL_HASH_UNAVAILABLE:
                print(
                    f"WARNING: failed to calculate xxHash for mod "
                    f"'{entry.mod.name}': {entry.error}",
                    file=sys.stderr,
                )

        outdated_entries = [
            entry for entry in entries if entry.status == _UpdateCheckStatus.OUTDATED
        ]
        if not outdated_entries:
            print("No outdated mods found.")
            return 0

        print("The following outdated mod(s) will be upgraded:")
        for entry in outdated_entries:
            mod_info = entry.mod_info
            assert mod_info is not None
            print(
                f"  - {entry.mod.name} "
                f"(v{entry.mod.version} -> v{mod_info.version}) "
                f"[{entry.mod.get_filename()}]"
            )

        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Skipped upgrading mods.")
            return 0

        exit_code = 0
        for entry in outdated_entries:
            exit_code = max(
                exit_code,
                self._upgrade_mod(entry.mod, entry.mod_info),
            )
        return exit_code

    def upgrade(
        self, args: list[str], prog_name: str = "celeste-mod-manager upgrade"
    ) -> int:
        """Update specified mod(s)"""
        parser = optparse.OptionParser(
            prog=prog_name,
            usage=f"{prog_name} MOD... | {prog_name} ALL",
            description="Upgrade installed mods. ALL must be the only argument.",
        )
        _, positionals = parser.parse_args(args)
        if len(positionals) == 0:
            print("ERROR: no mod specified to update.", file=sys.stderr)
            return 1
        if "ALL" in positionals and positionals != ["ALL"]:
            print("ERROR: ALL must be the only argument to upgrade.", file=sys.stderr)
            return 1
        if positionals == ["ALL"]:
            return self._upgrade_all()

        mod_info_index = self._load_update_mod_index()
        if mod_info_index is None:
            return 1

        scan_result = self._scan_installed_mods()
        if has_errors(scan_result.issues):
            return 1

        exit_code = 0
        for mod_name in positionals:
            mod = self._get_installed_mod_by_name(mod_name, scan_result.mods)
            if not mod:
                print(
                    f"ERROR: mod '{mod_name}' is not installed. Cannot update a mod that is not installed.",
                    file=sys.stderr,
                )
                exit_code = 1
                continue
            exit_code = max(
                exit_code,
                self._upgrade_mod(
                    mod,
                    mod_info_index.get(mod.name),
                    display_name=mod_name,
                ),
            )
        return exit_code
