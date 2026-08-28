import sys
from pathlib import Path
from loguru import logger

from . import config, mod_source
from .cli import CelesteModCLI
from .path import (
    CelestePathError,
    configure_celeste_dir,
    validate_mod_db_path,
    validate_mods_dir,
)
from .output import print_error

_KNOWN_COMMANDS = {
    "search",
    "list",
    "list-tree",
    "apply",
    "gc",
    "garbage-collect",
    "check-updates",
    "update-db",
    "upgrade",
    "everest",
}
_SUBCOMMANDS_WITH_HELP = {
    "list",
    "list-tree",
    "apply",
    "gc",
    "garbage-collect",
    "upgrade",
    "everest",
}
_DATABASE_COMMANDS = {"search", "apply", "check-updates", "update-db", "upgrade"}


class GlobalOptions:
    celeste_dir: Path | None = None
    log_level: str = config.DEFAULT_LOG_LEVEL
    mod_source: str | None = None


def cmd_help():
    print(
        f"""\
Usage: celeste-mod-manager [options] <command> [args]

Commands:
    help               Show this help message.
    search             Search for mods in the mod database.
    list               List all installed mods.
    list-tree          List enabled mods and their dependencies in a tree format.
    apply              Apply the desired mod state from a requirement file.
    gc, garbage-collect
                       Delete all currently disabled mod archives.
    check-updates      Check updates for installed mods. Entries in
                       Mods/updaterblacklist.txt are marked and not checked.
    update-db          Force update the local mod database from the server.
    upgrade            Upgrade selected mod(s), or use ALL to upgrade every
                       outdated mod after confirmation.
    everest            Interactively install or update Everest using the
                       official MiniInstaller.

Options:
    --celeste-dir <path>  Specify the Celeste directory, overriding config and
                          automatic discovery.
    --mod-source <source> Select the mod catalog and download source
                          (wegfan or gamebanana; default: wegfan).
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
                print_error("--celeste-dir requires a path value.")
                sys.exit(1)
            options.celeste_dir = Path(args[i + 1]).expanduser()
            i += 2
            continue
        elif arg == "--mod-source":
            if i + 1 >= len(args):
                print_error("--mod-source requires a value.")
                sys.exit(1)
            options.mod_source = args[i + 1]
            i += 2
            continue
        elif arg == "--help" or arg == "-h":
            cmd_help()
            sys.exit(0)
        elif arg == "--log-level":
            if i + 1 >= len(args):
                print_error("--log-level requires a value.")
                sys.exit(1)
            level = args[i + 1].upper()
            if level not in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                print_error(
                    f"invalid log level '{level}'. Valid levels are: TRACE, "
                    "DEBUG, INFO, WARNING, ERROR, CRITICAL."
                )
                sys.exit(1)
            options.log_level = level
            i += 2
            continue
        else:
            print_error(f"unknown argument '{arg}'")
            sys.exit(1)

    return args[i:], options, False


def _configure_logger(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level)


def _dispatch_cli(cli: CelesteModCLI, subcommand: str, extra_args: list[str]) -> int:
    if subcommand == "search":
        return cli.search(extra_args)
    if subcommand == "list":
        return cli.list_mods(extra_args)
    if subcommand == "list-tree":
        return cli.list_tree(extra_args, prog_name="celeste-mod-manager list-tree")
    if subcommand == "apply":
        return cli.apply(extra_args, prog_name="celeste-mod-manager apply")
    if subcommand in {"gc", "garbage-collect"}:
        return cli.garbage_collect(
            extra_args, prog_name=f"celeste-mod-manager {subcommand}"
        )
    if subcommand == "check-updates":
        return cli.check_updates(extra_args)
    if subcommand == "update-db":
        return cli.update_db(extra_args)
    if subcommand == "upgrade":
        return cli.upgrade(extra_args, prog_name="celeste-mod-manager upgrade")
    if subcommand == "everest":
        return cli.everest(extra_args, prog_name="celeste-mod-manager everest")
    raise AssertionError(f"cannot dispatch unknown command: {subcommand}")


def _run_cli() -> int:
    # Dispatch
    args, options, parse_error = _parse_global_args(sys.argv[1:])
    if parse_error:
        print_error("failed to parse arguments.")
        return 1

    if len(args) == 0:
        cmd_help()
        return 1
    subcommand = args[0]
    extra_args = args[1:]

    if subcommand == "help":
        cmd_help()
        return 0

    if subcommand not in _KNOWN_COMMANDS:
        print_error(f"unknown command '{subcommand}'")
        print()
        cmd_help()
        return 1

    if subcommand == "everest" and extra_args:
        return _dispatch_cli(CelesteModCLI(), subcommand, extra_args)

    if subcommand in _SUBCOMMANDS_WITH_HELP and extra_args in (
        ["-h"],
        ["--help"],
    ):
        return _dispatch_cli(CelesteModCLI(), subcommand, extra_args)

    _configure_logger(options.log_level)

    logger.debug(
        f"Global options: celeste_dir={options.celeste_dir!r}, "
        f"log_level={options.log_level!r}, mod_source={options.mod_source!r}; "
        f"Remaining args: {args!r}"
    )

    configure_celeste_dir(options.celeste_dir)
    if subcommand != "everest":
        validate_mods_dir()
    if subcommand in _DATABASE_COMMANDS:
        mod_source.configure(options.mod_source)
        validate_mod_db_path(mod_source.get_cache_filename())

    return _dispatch_cli(CelesteModCLI(), subcommand, extra_args)


def main() -> int:
    try:
        _configure_logger(config.DEFAULT_LOG_LEVEL)
        return _run_cli()
    except CelestePathError as e:
        print_error(str(e))
        return 1
    except mod_source.InvalidModSourceError as e:
        print_error(str(e))
        return 1
    except KeyboardInterrupt:
        print("Cancelled by user.", file=sys.stderr)
        return 130
    except Exception as e:
        logger.opt(exception=e).debug("Unhandled exception at CLI boundary.")
        print_error(
            "an unexpected internal error occurred. "
            "Re-run with --log-level DEBUG for details."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
