import sys
from pathlib import Path
from loguru import logger

from . import config
from .cli import CelesteModCLI
from .path import get_celeste_dir, set_mod_paths


class GlobalOptions:
    celeste_dir: Path | None = None
    log_level: str = config.DEFAULT_LOG_LEVEL


def cmd_help():
    print(
        f"""\
Usage: celeste-mod-manager [options] <command> [args]

Commands:
    help               Show this help message.
    search             Search for mods in the mod database.
    list               List all installed mods.
    list-tree          List all installed mods and their dependencies in a tree format.
    install            Install some mod(s).
    apply              EXPERIMENTAL: Apply Mods/required_mods.txt declaratively.
    uninstall          Uninstall root mod(s).
    disable            Disable mod(s) by adding them to Mods/blacklist.txt.
    enable             Enable mod(s) by removing them from Mods/blacklist.txt.
    check-updates      Check updates for installed mods.
    update-db          Force update the local mod database from the server.
    upgrade            Upgrade some mod(s) to the latest version.

Options:
    --celeste-dir <path>  Specify the path to the Celeste directory.
    --log-level <level>   Set the log level (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL).""",
        file=sys.stderr,
    )


def _parse_global_args(args: list[str]) -> tuple[list[str], GlobalOptions, bool]:
    options = GlobalOptions()

    i = 0
    while i < len(args):
        arg = args[i]
        if not arg.startswith("-") and not arg.startswith("--"):
            break

        if arg == "--celeste-dir":
            if i + 1 >= len(args):
                print("ERROR: --celeste-dir requires a path value.", file=sys.stderr)
                sys.exit(1)
            options.celeste_dir = Path(args[i + 1]).expanduser()
            i += 2
            continue
        elif arg == "--help" or arg == "-h":
            cmd_help()
            sys.exit(0)
        elif arg == "--log-level":
            if i + 1 >= len(args):
                print("ERROR: --log-level requires a value.", file=sys.stderr)
                sys.exit(1)
            level = args[i + 1].upper()
            if level not in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                print(
                    f"ERROR: invalid log level '{level}'. Valid levels are: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL.",
                    file=sys.stderr,
                )
                sys.exit(1)
            options.log_level = level
            i += 2
            continue
        else:
            print(f"ERROR: unknown argument '{arg}'", file=sys.stderr)
            sys.exit(1)

    return args[i:], options, False


def main():
    cli = CelesteModCLI()

    # Dispatch
    args, options, parse_error = _parse_global_args(sys.argv[1:])
    if parse_error:
        print(f"ERROR: failed to parse arguments.", file=sys.stderr)
        return 1

    if len(args) == 0:
        cmd_help()
        return 1
    subcommand = args[0]
    extra_args = args[1:]

    if subcommand == "help":
        cmd_help()
        return 0

    logger.remove()
    logger.add(sys.stderr, level=options.log_level)

    logger.debug(f"Global options: {options}, Remaining args: {args}")

    if options.celeste_dir is not None:
        if options.celeste_dir.exists() and options.celeste_dir.is_dir():
            logger.debug(
                f"Using Celeste directory from command line: {options.celeste_dir}"
            )
            set_mod_paths(options.celeste_dir)
        else:
            print(
                f"ERROR: specified Celeste directory '{options.celeste_dir}' does not exist or is not a directory.",
                file=sys.stderr,
            )
            return 1
    else:
        celeste_dir = get_celeste_dir()
        if celeste_dir is None:
            print(
                "ERROR: Could not find Celeste installation directory. Please make sure Celeste is installed.",
                file=sys.stderr,
            )
            return 1
        set_mod_paths(celeste_dir)

    if subcommand == "search":
        cli.search(extra_args)
    elif subcommand == "list":
        cli.list_mods(extra_args)
    elif subcommand == "list-tree":
        return cli.list_tree(extra_args, prog_name=f"celeste-mod-manager list-tree")
    elif subcommand == "install":
        return cli.install(extra_args, prog_name=f"celeste-mod-manager install")
    elif subcommand == "apply":
        return cli.apply(extra_args, prog_name=f"celeste-mod-manager apply")
    elif subcommand == "uninstall":
        return cli.uninstall(extra_args, prog_name=f"celeste-mod-manager uninstall")
    elif subcommand == "disable":
        return cli.disable(extra_args, prog_name=f"celeste-mod-manager disable")
    elif subcommand == "enable":
        return cli.enable(extra_args, prog_name=f"celeste-mod-manager enable")
    elif subcommand == "check-updates":
        return cli.check_updates(extra_args)
    elif subcommand == "update-db":
        return cli.update_db(extra_args)
    elif subcommand == "upgrade":
        return cli.upgrade(extra_args, prog_name=f"celeste-mod-manager upgrade")
    else:
        print(f"ERROR: unknown command '{subcommand}'", file=sys.stderr)
        print()
        cmd_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
