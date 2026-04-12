import sys

from .cli import CelesteModCLI

cli = CelesteModCLI()

def cmd_help():
    print(f"""\
Usage: {sys.argv[0]} <command> [args]

Commands:
    help               Show this help message.
    search             Search for mods in the mod database.
    list               List all installed mods.
    list-tree          List all installed mods and their dependencies in a tree format.
    install            Install a mod by its exact name.\
""", file=sys.stderr)

def main():
    cli = CelesteModCLI()
    # Dispatch
    args = sys.argv[1:]
    if len(args) == 0:
        cmd_help()
        return 1
    command = args[0]
    extra_args = args[1:]
    if command == "help" or command == "--help" or command == "-h":
        cmd_help()
        return 0
    elif command == "search":
        cli.search(extra_args)
    elif command == "list":
        cli.list(extra_args)
    elif command == "list-tree":
        cli.list_tree(extra_args)
    elif command == "install":
        return(cli.install(extra_args))
    else:
        print(f"ERROR: unknown command '{command}'", file=sys.stderr)
        print()
        cmd_help()
        return 1

if __name__ == "__main__":
    sys.exit(main())