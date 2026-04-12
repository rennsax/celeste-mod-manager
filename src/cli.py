import sys
from typing import List

from .mod_db import search_mod_in_db, pretty_print_mod_info, get_mod_info
from .mod_manager import get_installed_mods, pretty_print_mods, analyse_mod_deps, _download_mod, resolve_deps

class CelesteModCLI:

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

    def list_tree(self, args: List[str]) -> None:
        """List all installed mods and their dependencies in a tree format."""
        analyse_mod_deps(maxdepth=2, optional=False)

    def install(self, args: List[str]) -> int:
        """Install a mod by its exact name."""
        mod_name = args[0]
        mod_info = get_mod_info(mod_name)
        if not mod_info:
            print(f"Mod '{mod_name}' not found in the database.", file=sys.stderr)
            return 1
        mod = _download_mod(mod_info)
        if not mod:
            print(f"Failed to download mod '{mod_name}'.", file=sys.stderr)
            return 1
        resolved_deps, failed_deps = resolve_deps(mod)
        if len(resolved_deps) != 0:
            print(f"Also install the following dependencies:")
            for dep in resolved_deps:
                print(f"  - {dep.name} (v{dep.version})")
        if len(failed_deps) != 0:
            print(f"Failed to install the following dependencies:")
            for dep in failed_deps:
                print(f"  - {dep}")
            return 1
        return 0
