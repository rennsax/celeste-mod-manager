import sys
import os
from typing import List, Sequence
import optparse
import textwrap
from loguru import logger

from .mod_db import search_mod_in_db, pretty_print_mod_info
from .mod_manager import get_installed_mods, pretty_print_mods, analyse_mod_deps, ensure_mod, resolve_deps

class CelesteModCLI:

    def _install_mod(self, mod_name: str, no_dep: bool = False, optional_deps: bool = False) -> bool:
        mod = ensure_mod(mod_name)
        if not mod:
            return False

        if no_dep:
            return True

        resolved_deps, failed_deps = resolve_deps(mod, optional=optional_deps)
        if len(resolved_deps) != 0:
            print("Also install the following dependencies:")
            for dep in resolved_deps:
                print(f"  - {dep.name} (v{dep.version})")
            print()
        if len(failed_deps) != 0:
            print(f"ERROR: Failed to install the dependencies for {mod_name}: {", ".join(map(lambda m: f'{m}', failed_deps))}.")
            return False
        return True

    def install(self, args: Sequence[str], prog_name: str = "celeste-mod-manager install") -> int:
        """Install a single mod or install from a requirement file."""
        def show_help() -> None:
            print(textwrap.dedent(f"""\
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
                  --optional-deps  Also include optional dependencies when resolving dependencies."""))
        parser = optparse.OptionParser(
            prog=prog_name,
            add_help_option=False,
            usage="",
        )
        parser.add_option("-r", "--requirement", dest="requirement", metavar="FILE")
        parser.add_option("-h", "--help", action="store_true", dest="help")
        parser.add_option("--no-deps", action="store_true", dest="no_deps", default=False)
        parser.add_option("--optional-deps", action="store_true", dest="optional_deps", default=False)

        options, positionals = parser.parse_args(list(args))
        logger.debug(f"Parsed options: {options}, positionals: {positionals}")

        if options.help:
            show_help()
            return 0
        if options.requirement and len(positionals) > 0:
            print("ERROR: cannot specify both a requirement file and some mod name(s).", file=sys.stderr)
            return 1
        if not options.requirement and len(positionals) == 0:
            print("ERROR: no mod specified to install.", file=sys.stderr)
            return 1

        mods_to_install: List[str] = list()
        if options.requirement:
            if not os.path.isfile(options.requirement):
                print(f"ERROR: requirement file '{options.requirement}' not found.", file=sys.stderr)
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
            print(f"Try to install mod '{mod_name}'...")
            if not self._install_mod(mod_name, no_dep=options.no_deps, optional_deps=options.optional_deps):
                print(f"\033[91mFailed to install '{mod_name}'.\033[0m", file=sys.stderr)
                exit_code = 1
            print(f"Successfully installed '{mod_name}'.\n")

        return exit_code

    def search(self, args: List[str]) -> None:
        """Search for mods in the database and print their information."""
        pattern = args[0]
        found_mods = search_mod_in_db(lambda m: pattern.lower() in m.name.lower())
        if not found_mods:
            print(f"No mods found.")
            return

        print(f"Found {len(found_mods)} mod(s) :")
        print("-" * 40)

        for mod in found_mods:
            pretty_print_mod_info(mod)
            print("-" * 40)

    def list(self, args: List[str]) -> None:
        """List all installed mods."""
        mods = get_installed_mods()
        pretty_print_mods(mods)

    def list_tree(self, args: List[str], prog_name: str = "celeste-mod-manager list-tree") -> int:
        """List all installed mods and their dependencies in a tree format."""
        parser = optparse.OptionParser(
            prog=prog_name,
        )
        parser.add_option("--maxdepth", "-d", dest="max_depth", type="int", default=2, help="Maximum depth of the dependency tree to display. (Default: 2)")
        parser.add_option("--optional-deps", action="store_true", dest="optional_deps", default=False, help="Also include optional dependencies in the tree.")
        options, _ = parser.parse_args(args)
        if options.max_depth <= 0:
            print("ERROR: max depth must be a positive integer.", file=sys.stderr)
            return 1
        analyse_mod_deps(maxdepth=options.max_depth, optional=options.optional_deps)
        return 0

if __name__ == "__main__":
    cli = CelesteModCLI()
    args = "-r required_mods.txt".split()
    cli.install(args)