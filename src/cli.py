import sys
import os
from typing import Sequence
import optparse
import textwrap
from loguru import logger

from . import mod_manager, mod_db
from .mod_manager import EnsureModStatus
from . import config


class CelesteModCLI:

    def _install_mod(
        self, mod_name: str, no_dep: bool = False, optional_deps: bool = False
    ) -> bool:
        print(f"Processing {mod_name}")
        mod, _status = mod_manager.ensure_mod(mod_name, root=True)
        if not mod:
            if _status == EnsureModStatus.NOT_FOUND_IN_DB:
                print(f"ERROR: mod '{mod_name}' not found in the database.")
            elif _status == EnsureModStatus.DOWNLOAD_FAILED:
                print(f"ERROR: failed to download mod '{mod_name}'.")
            elif _status == EnsureModStatus.UNEXPECTED:
                print(
                    f"ERROR: failed to install mod '{mod_name}' due to an unexpected error."
                )
            return False

        assert _status in {EnsureModStatus.INSTALLED, EnsureModStatus.ALREADY_EXISTS}

        if _status == EnsureModStatus.ALREADY_EXISTS:
            print(f"Requirement already satisfied: {mod.name} ({mod.version})")

        if no_dep:
            return True

        print(f"Installing dependencies for {mod.name}")
        resolved_deps, failed_deps = mod_manager.resolve_deps(
            mod, optional=optional_deps
        )
        if len(resolved_deps) != 0:
            print("Installed dependencies:")
            for dep in resolved_deps:
                print(f"  - {dep.name} (v{dep.version})")
            print()
        elif len(failed_deps) == 0:
            print("No dependencies to install.")
        if len(failed_deps) != 0:
            failed_deps_str = ", ".join(str(mod) for mod in failed_deps)
            print(
                f"ERROR: Failed to install the dependencies for {mod_name}: {failed_deps_str}."
            )
            return False

        disabled_required_mods = mod_manager.get_disabled_required_mods(
            mod, optional=optional_deps
        )
        if disabled_required_mods:
            print(
                "The following locally installed mod(s) are required to run "
                f"{mod.name}, but are disabled by the blacklist:"
            )
            for required_mod in disabled_required_mods:
                print(
                    f"  - {required_mod.name} "
                    f"(v{required_mod.version}) [{required_mod.get_filename()}]"
                )
            answer = input("Enable them now? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Skipped enabling required mods.")
                return False
            if not mod_manager.enable_mods(disabled_required_mods):
                print("ERROR: failed to enable required mods.", file=sys.stderr)
                return False
            print("Successfully enabled required mods.")
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
            if not self._install_mod(
                mod_name, no_dep=options.no_deps, optional_deps=options.optional_deps
            ):
                print(
                    f"\033[91mFailed to install '{mod_name}'.\033[0m", file=sys.stderr
                )
                exit_code = 1
            else:
                print(f"Successfully installed '{mod_name}'.\n")

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

        print(f"Found {len(found_mods)} mod(s) :")
        print("-" * 40)

        for mod in found_mods:
            mod_db.pretty_print_mod_info(mod)
            print("-" * 40)

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
        parser.add_option(
            "--enabled",
            action="store_true",
            dest="enabled_only",
            default=False,
            help="Only list enabled mods.",
        )
        options, _ = parser.parse_args(args)
        mods = []
        if options.root_only:
            logger.debug("Listing only root mods.")
            mods = mod_manager.get_root_mods()
        else:
            mods = mod_manager.get_installed_mods()
        if options.enabled_only:
            blacklisted_filenames = mod_manager.get_blacklisted_mod_filenames()
            mods = [
                mod for mod in mods if mod.get_filename() not in blacklisted_filenames
            ]
        mod_manager.pretty_print_mods(mods, show_enabled=not options.enabled_only)

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
        parser.add_option(
            "--enabled",
            action="store_true",
            dest="enabled_only",
            default=False,
            help="Only include enabled mods in the tree.",
        )
        options, _ = parser.parse_args(args)
        if options.max_depth <= 0:
            print("ERROR: max depth must be a positive integer.", file=sys.stderr)
            return 1
        mod_manager.analyse_mod_deps(
            maxdepth=options.max_depth,
            optional=options.optional_deps,
            enabled_only=options.enabled_only,
        )
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

    def uninstall(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager uninstall"
    ) -> int:
        """Uninstall root mod(s) and dependencies that only they require."""
        parser = optparse.OptionParser(prog=prog_name)
        parser.add_option(
            "-f",
            "--force",
            action="store_true",
            dest="force",
            default=False,
            help="Force uninstall specified mod(s), even if they are not root mods.",
        )
        options, positionals = parser.parse_args(list(args))

        if len(positionals) == 0:
            print("ERROR: no mod specified to uninstall.", file=sys.stderr)
            return 1

        if not config._ENABLE_ROOT_INSTALL_TRACK:
            print(
                "ERROR: uninstall is not implemented when root install tracking is disabled.",
                file=sys.stderr,
            )
            return 1

        planned_mods_by_name: dict[str, mod_manager.Mod] = {}
        skipped_mods = 0
        valid_mod_names = []
        for mod_name in positionals:
            mods_to_uninstall, status = mod_manager.build_uninstall_plan(
                mod_name, force=options.force
            )
            if status == mod_manager.UninstallModStatus.NOT_INSTALLED:
                print(f"ERROR: mod '{mod_name}' is not installed.", file=sys.stderr)
                skipped_mods += 1
                continue
            if status == mod_manager.UninstallModStatus.NOT_RECORDED_ROOT:
                print(
                    f"ERROR: mod '{mod_name}' is not a recorded root mod. Uninstalling it may break other installed mods.",
                    file=sys.stderr,
                )
                skipped_mods += 1
                continue
            if status == mod_manager.UninstallModStatus.UNEXPECTED:
                print(
                    f"ERROR: failed to build uninstall plan for mod '{mod_name}'.",
                    file=sys.stderr,
                )
                skipped_mods += 1
                continue
            if status == mod_manager.UninstallModStatus.ROOT_TRACK_DISABLED:
                print(
                    "ERROR: uninstall is not implemented when root install tracking is disabled.",
                    file=sys.stderr,
                )
                return 1

            valid_mod_names.append(mod_name)
            for mod in mods_to_uninstall:
                planned_mods_by_name[mod.name] = mod

        if not planned_mods_by_name:
            print("ERROR: no valid mod specified to uninstall.", file=sys.stderr)
            return 1

        mods_to_uninstall = sorted(
            planned_mods_by_name.values(), key=lambda mod: mod.name.lower()
        )
        requested_mods = ", ".join(valid_mod_names)
        if options.force:
            print(
                f"Force uninstall is enabled. The following mod(s) will be uninstalled for: {requested_mods}"
            )
        else:
            print(f"The following mod(s) will be uninstalled for: {requested_mods}")
        for mod in mods_to_uninstall:
            print(f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]")

        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Skipped uninstalling mods.")
            return 0

        if mod_manager.uninstall_mods(mods_to_uninstall):
            print("Successfully uninstalled mods.")
            return 1 if skipped_mods else 0

        print("ERROR: failed to uninstall mods.", file=sys.stderr)
        return 1

    def _toggle_mods(
        self,
        args: Sequence[str],
        action: str,
        prog_name: str,
    ) -> int:
        parser = optparse.OptionParser(prog=prog_name)
        _, positionals = parser.parse_args(list(args))

        if len(positionals) == 0:
            print(f"ERROR: no mod specified to {action}.", file=sys.stderr)
            return 1

        planned_mods_by_name: dict[str, mod_manager.Mod] = {}
        skipped_mods = 0
        valid_mod_names = []
        build_plan = (
            mod_manager.build_disable_plan
            if action == "disable"
            else mod_manager.build_enable_plan
        )
        gerund_action = "disabling" if action == "disable" else "enabling"

        for mod_name in positionals:
            mods_to_toggle, status = build_plan(mod_name)
            if status == mod_manager.ModToggleStatus.NOT_INSTALLED:
                print(f"ERROR: mod '{mod_name}' is not installed.", file=sys.stderr)
                skipped_mods += 1
                continue
            if status == mod_manager.ModToggleStatus.NOT_RECORDED_ROOT:
                print(
                    f"ERROR: mod '{mod_name}' is not a recorded root mod. "
                    f"{gerund_action.capitalize()} it may cause other mods to fail loading.",
                    file=sys.stderr,
                )
                skipped_mods += 1
                continue
            if status == mod_manager.ModToggleStatus.ROOT_TRACK_DISABLED:
                print(
                    f"ERROR: {action} is not implemented when root install tracking is disabled.",
                    file=sys.stderr,
                )
                return 1
            if status == mod_manager.ModToggleStatus.ALREADY_DISABLED:
                print(f"Mod '{mod_name}' is already disabled.")
                continue
            if status == mod_manager.ModToggleStatus.ALREADY_ENABLED:
                print(f"Mod '{mod_name}' and its dependencies are already enabled.")
                continue
            if status == mod_manager.ModToggleStatus.UNEXPECTED:
                print(
                    f"ERROR: failed to build {action} plan for mod '{mod_name}'.",
                    file=sys.stderr,
                )
                skipped_mods += 1
                continue

            valid_mod_names.append(mod_name)
            for mod in mods_to_toggle:
                planned_mods_by_name[mod.name] = mod

        if not planned_mods_by_name:
            print(f"No mods to {action}.")
            return 1 if skipped_mods else 0

        mods_to_toggle = sorted(
            planned_mods_by_name.values(), key=lambda mod: mod.name.lower()
        )
        requested_mods = ", ".join(valid_mod_names)
        blacklist_action = "added to" if action == "disable" else "removed from"
        print(
            f"The following mod(s) will be {blacklist_action} blacklist.txt for: {requested_mods}"
        )
        for mod in mods_to_toggle:
            print(f"  - {mod.name} (v{mod.version}) [{mod.get_filename()}]")

        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print(f"Skipped {gerund_action} mods.")
            return 0

        toggle_mods = (
            mod_manager.disable_mods if action == "disable" else mod_manager.enable_mods
        )
        if toggle_mods(mods_to_toggle):
            past_action = "disabled" if action == "disable" else "enabled"
            print(f"Successfully {past_action} mods.")
            return 1 if skipped_mods else 0

        print(f"ERROR: failed to {action} mods.", file=sys.stderr)
        return 1

    def disable(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager disable"
    ) -> int:
        """Disable mod(s) by adding them and exclusive dependencies to blacklist.txt."""
        return self._toggle_mods(args, action="disable", prog_name=prog_name)

    def enable(
        self, args: Sequence[str], prog_name: str = "celeste-mod-manager enable"
    ) -> int:
        """Enable mod(s) by removing them and disabled dependencies from blacklist.txt."""
        return self._toggle_mods(args, action="enable", prog_name=prog_name)

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
