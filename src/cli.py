import sys
import os
from typing import Sequence
import optparse
import textwrap
from loguru import logger

import questionary

from . import config, mod_db, mod_manager
from .mod_manager import (
    EnsureModStatus,
    blacklist_key,
    disable_closure,
    enable_closure,
    partition_installed_mods,
)
from .blacklist import read_blacklist, write_blacklist
from .colors import color
from .pager import paged_output


class CelesteModCLI:

    def _install_mod(
        self, mod_name: str, no_dep: bool = False, optional_deps: bool = False
    ) -> bool:
        mod, _status = mod_manager.ensure_mod(mod_name, root=True)
        if not mod:
            if _status == EnsureModStatus.NOT_FOUND_IN_DB:
                print(f"ERROR: mod '{color(mod_name, 'cyan')}' not found in the database.")
            elif _status == EnsureModStatus.DOWNLOAD_FAILED:
                print(f"ERROR: failed to download mod '{color(mod_name, 'cyan')}'.")
            elif _status == EnsureModStatus.UNEXPECTED:
                print(
                    f"ERROR: failed to install mod '{color(mod_name, 'cyan')}' due to an unexpected error."
                )
            return False

        assert _status in {EnsureModStatus.INSTALLED, EnsureModStatus.ALREADY_EXISTS}

        if _status == EnsureModStatus.ALREADY_EXISTS:
            print(f"Mod '{color(mod_name, 'cyan')}' {color('already exists', 'yellow')} locally.")

        if no_dep:
            return True

        resolved_deps, failed_deps = mod_manager.resolve_deps(
            mod, optional=optional_deps
        )
        if len(resolved_deps) != 0:
            print(color("Also install the following dependencies:", "bold"))
            for dep in resolved_deps:
                name = color(dep.name, "cyan")
                version = color(f"v{dep.version}", "green")
                print(f"  - {name} ({version})")
            print()
        if len(failed_deps) != 0:
            failed_deps_str = ", ".join(color(str(mod), "cyan") for mod in failed_deps)
            print(
                f"ERROR: Failed to install the dependencies for {color(mod_name, 'cyan')}: {failed_deps_str}."
            )
            return False
        return True

    def install(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager install"
    ) -> int:
        """Install a single mod or install from a requirement file."""

        def show_help() -> None:
            print(
                textwrap.dedent(
                    f"""\
                Usage:
                  {prog_name} [options] MOD...
                    Install some mod(s).

                  {prog_name} [options] -r FILE
                    Install mods declaratively from the mods listed in FILE, one per line.

                  {prog_name} --help | -h
                    Show this help message.

                Examples:
                  {prog_name} StrawberryJam2021
                  {prog_name} -r required_mods.txt

                Options:
                  --no-deps        Do not resolve and install mod dependencies.
                  --optional-deps  Also include optional dependencies when resolving dependencies."""
                )
            )

        parser = optparse.OptionParser(
            prog=prog_name,
            add_help_option=False,
            usage="",
        )
        parser.add_option("-r", "--requirement", dest="requirement", metavar="FILE")
        parser.add_option("-h", "--help", action="store_true", dest="help")
        parser.add_option(
            "--no-deps", action="store_true", dest="no_deps", default=False
        )
        parser.add_option(
            "--optional-deps", action="store_true", dest="optional_deps", default=False
        )

        options, positionals = parser.parse_args(list(args))
        logger.debug(f"Parsed options: {options}, positionals: {positionals}")

        if options.help:
            show_help()
            return 0
        if options.requirement and len(positionals) > 0:
            print(
                "ERROR: cannot specify both a requirement file and some mod name(s).",
                file=sys.stderr,
            )
            return 1
        if not options.requirement and len(positionals) == 0:
            print("ERROR: no mod specified to install.", file=sys.stderr)
            return 1

        # The root mods to install
        mods_to_install: list[str] = list()

        if options.requirement:
            if not os.path.isfile(options.requirement):
                print(
                    f"ERROR: requirement file '{options.requirement}' not found.",
                    file=sys.stderr,
                )
                return 1
            with open(options.requirement, "r", encoding="utf-8") as f:
                for raw_line in f:
                    mod_name = raw_line.strip()
                    if not mod_name or mod_name.startswith("#"):
                        continue
                    mods_to_install.append(mod_name)
        elif len(positionals) > 0:
            mods_to_install = positionals

        if len(mods_to_install) == 0:
            print("WARNING: no mod specified to install.", file=sys.stderr)
            return 1

        exit_code = 0
        for mod_name in mods_to_install:
            print(f"Try to install mod '{color(mod_name, 'cyan')}'...")
            if not self._install_mod(
                mod_name, no_dep=options.no_deps, optional_deps=options.optional_deps
            ):
                print(
                    color(f"Failed to install '{mod_name}'.", "red"), file=sys.stderr
                )
                exit_code = 1
            else:
                print(color(f"Successfully installed '{mod_name}'.", "green") + "\n")

        return exit_code

    def search(self, args: list[str]) -> None:
        """Search for mods in the database and print their information."""
        pattern = args[0]
        found_mods = mod_db.search_mod_in_db(
            lambda m: pattern.lower() in m.name.lower()
        )
        if not found_mods:
            print(f"No mods found.")
            return

        found_mods.sort(key=lambda m: m.submissionFile.downloads, reverse=True)

        with paged_output():
            print(color(f"Found {len(found_mods)} mod(s) :", "bold"))
            sep = color("-" * 40, "dim")
            print(sep)

            for mod in found_mods:
                mod_db.pretty_print_mod_info(mod)
                print(sep)

    def list_mods(
        self, args: list[str], prog_name: str = "celeste-mod-manager list"
    ) -> None:
        """List installed mods."""
        parser = optparse.OptionParser(prog=prog_name)
        parser.add_option(
            "--root",
            action="store_true",
            dest="root_only",
            default=False,
            help="Only list root mods (i.e. mods that are directly installed by the user).",
        )
        options, _ = parser.parse_args(args)
        mods = []
        if options.root_only:
            logger.debug("Listing only root mods.")
            mods = mod_manager.get_root_mods()
        else:
            mods = mod_manager.get_installed_mods()
        with paged_output():
            mod_manager.pretty_print_mods(mods)

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
        with paged_output():
            mod_manager.analyse_mod_deps(
                maxdepth=options.max_depth, optional=options.optional_deps
            )
        return 0

    @staticmethod
    def _parse_pattern_args(
        args: Sequence[str], prog_name: str, action: str
    ) -> tuple[str | None, int | None]:
        """Returns ``(pattern_or_none, exit_code_if_should_return)``."""
        parser = optparse.OptionParser(
            prog=prog_name,
            add_help_option=False,
            usage=f"{prog_name} [PATTERN]",
        )
        parser.add_option("-h", "--help", action="store_true", dest="help")
        options, positionals = parser.parse_args(list(args))
        if options.help:
            print(
                textwrap.dedent(
                    f"""\
                    Usage:
                      {prog_name} [PATTERN]
                        Pick installed mods to {action} from an interactive checkbox.
                        PATTERN (case-insensitive substring) filters the candidate list.
                        Mods related by required dependencies are automatically included.

                      {prog_name} --help | -h
                        Show this help message."""
                )
            )
            return None, 0
        if len(positionals) > 1:
            print(
                f"ERROR: at most one PATTERN allowed, got: {positionals}",
                file=sys.stderr,
            )
            return None, 1
        return (positionals[0] if positionals else None), None

    @staticmethod
    def _ask_checkbox(prompt: str, mods: list) -> list | None:
        """Run questionary checkbox; return None on Ctrl-C / empty."""
        choices = [
            questionary.Choice(title=f"{m.name}  ({m.version})", value=m) for m in mods
        ]
        try:
            picked = questionary.checkbox(prompt, choices=choices).ask()
        except KeyboardInterrupt:
            return None
        return picked

    def disable(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager disable"
    ) -> int:
        """Interactively disable installed mods via blacklist.txt."""
        pattern, early_exit = self._parse_pattern_args(args, prog_name, "disable")
        if early_exit is not None:
            return early_exit

        enabled, _disabled = partition_installed_mods()
        candidates = [
            m for m in enabled if pattern is None or pattern.lower() in m.name.lower()
        ]
        if not candidates:
            msg = (
                f"No enabled mods match '{pattern}'."
                if pattern
                else "No enabled mods to disable."
            )
            print(color(msg, "yellow"))
            return 0

        candidates.sort(key=lambda m: m.name.lower())
        selected = self._ask_checkbox(
            "Select mods to disable (Space to toggle, Enter to confirm):",
            candidates,
        )
        if not selected:
            print("Nothing selected. No changes made.")
            return 0

        full = disable_closure(selected, enabled)
        extras = [m for m in full if m not in selected]
        if extras:
            print(
                color(
                    "These mods will also be disabled (they depend on your selection):",
                    "yellow",
                )
            )
            for m in extras:
                print(f"  - {color(m.name, 'cyan')} ({color(m.version, 'green')})")
            try:
                go = questionary.confirm("Continue?", default=True).ask()
            except KeyboardInterrupt:
                go = False
            if not go:
                print("Aborted.")
                return 0

        comments, current = read_blacklist()
        new_set = current | {blacklist_key(m) for m in full}
        write_blacklist(comments, new_set)

        print(color(f"Disabled {len(full)} mod(s):", "green"))
        for m in full:
            print(f"  - {color(m.name, 'cyan')} ({color(m.version, 'green')})")
        return 0

    def enable(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager enable"
    ) -> int:
        """Interactively enable previously-disabled mods."""
        pattern, early_exit = self._parse_pattern_args(args, prog_name, "enable")
        if early_exit is not None:
            return early_exit

        enabled, disabled = partition_installed_mods()
        candidates = [
            m for m in disabled if pattern is None or pattern.lower() in m.name.lower()
        ]
        if not candidates:
            msg = (
                f"No disabled mods match '{pattern}'."
                if pattern
                else "No disabled mods to enable."
            )
            print(color(msg, "yellow"))
            return 0

        candidates.sort(key=lambda m: m.name.lower())
        selected = self._ask_checkbox(
            "Select mods to enable (Space to toggle, Enter to confirm):",
            candidates,
        )
        if not selected:
            print("Nothing selected. No changes made.")
            return 0

        full, missing = enable_closure(selected, disabled, enabled)
        extras = [m for m in full if m not in selected]
        if extras:
            print(
                color(
                    "These required deps are currently disabled and will also be enabled:",
                    "yellow",
                )
            )
            for m in extras:
                print(f"  - {color(m.name, 'cyan')} ({color(m.version, 'green')})")
            try:
                go = questionary.confirm("Continue?", default=True).ask()
            except KeyboardInterrupt:
                go = False
            if not go:
                print("Aborted.")
                return 0

        if missing:
            print(
                color(
                    "Warning: required deps are not installed (Everest may refuse to load):",
                    "yellow",
                )
            )
            for n in missing:
                print(f"  - {color(n, 'cyan')}")

        comments, current = read_blacklist()
        new_set = current - {blacklist_key(m) for m in full}
        write_blacklist(comments, new_set)

        print(color(f"Enabled {len(full)} mod(s):", "green"))
        for m in full:
            print(f"  - {color(m.name, 'cyan')} ({color(m.version, 'green')})")
        return 0

    def check_updates(self, args: list[str]) -> int:
        """Check for updates for all installed mods."""
        mods = mod_manager.get_installed_mods()
        if not mods:
            print("No mods installed.")
            return 0

        name_width = max(len(mod.name) for mod in mods)
        status_width = len("[OUTDATED]")
        up_to_date_count = 0
        update_available_count = 0
        skipped_count = 0

        print("-" * 72)
        print(f"{'Status':<{status_width}}  {'Mod':<{name_width}}  Version")
        print("-" * 72)

        for mod in mods:
            cur_mod_info = mod_db.get_mod_info(mod.name)
            if cur_mod_info is None:
                print(
                    f"{'[SKIP]':<{status_width}}  {mod.name:<{name_width}}  local={mod.version}  remote=unknown"
                )
                skipped_count += 1
                continue
            # TODO: other version comparison logic?
            if cur_mod_info.version != mod.version:
                print(
                    f"\033[93m{'[OUTDATED]':<{status_width}}\033[0m  {mod.name:<{name_width}}  {mod.version} -> {cur_mod_info.version}"
                )
                update_available_count += 1
            else:
                print(
                    f"\033[92m{'[OK]':<{status_width}}\033[0m  {mod.name:<{name_width}}  {mod.version}"
                )
                up_to_date_count += 1

        print("-" * 72)
        print(
            f"Summary: total={len(mods)}, outdated={update_available_count}, "
            f"up-to-date={up_to_date_count}, skipped={skipped_count}"
        )
        return 0

    def _get_installed_mod_by_name(self, mod_name: str) -> mod_manager.Mod | None:
        """Get an installed mod by its name. Return None if not found."""
        mods = mod_manager.get_installed_mods()
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

    def upgrade(
        self, args: list[str], prog_name: str = "celeste-mod-manager upgrade"
    ) -> int:
        """Update specified mod(s)"""
        parser = optparse.OptionParser(prog=prog_name)
        options, positionals = parser.parse_args(args)
        if len(positionals) == 0:
            print("ERROR: no mod specified to update.", file=sys.stderr)
            return 1
        exit_code = 0
        for mod_name in positionals:
            mod = self._get_installed_mod_by_name(mod_name)
            if not mod:
                print(
                    f"ERROR: mod '{mod_name}' is not installed. Cannot update a mod that is not installed.",
                    file=sys.stderr,
                )
                exit_code = 1
                continue
            logger.info(f"Try to update mod '{mod_name}'...")
            updated_mod, status = mod_manager.update_mod(mod)
            if not updated_mod:
                if status == mod_manager.UpdateModStatus.DOWNLOAD_FAILED:
                    print(f"ERROR: failed to download the update for mod '{mod_name}'.")
                    exit_code = 1
                elif status == mod_manager.UpdateModStatus.UNEXPECTED:
                    print(
                        f"ERROR: failed to update mod '{mod_name}' due to an unexpected error."
                    )
                elif status == mod_manager.UpdateModStatus.ALREADY_UP_TO_DATE:
                    print(f"'{mod_name}' is already up to date.")
                exit_code = 1
            else:
                assert status == mod_manager.UpdateModStatus.UPDATED
                print(
                    f"Successfully updated '{mod_name}' from v{mod.version} to v{updated_mod.version}.\n"
                )
        return exit_code


if __name__ == "__main__":
    cli = CelesteModCLI()
    args = "-r required_mods.txt".split()
    cli.install(args)
