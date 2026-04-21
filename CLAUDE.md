# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install in editable mode for development
pip install -e .[dev]

# Run the CLI from a checkout (entry point: celeste_mod_manager.main:main)
celeste-mod-manager help
python -m celeste_mod_manager.main help

# Format (Black, line length 88, target py310)
black src
```

There is no test suite, no linter beyond Black, and no CI configured.

## Source layout quirk

`pyproject.toml` rewrites `src/` into the package `celeste_mod_manager` at build time
(`[tool.hatch.build.targets.wheel.sources]`). On disk the modules live directly under
`src/` and import each other with relative imports (`from . import config`). This means:

- The package is only importable after `pip install` (editable or otherwise) — running
  files directly from `src/` will fail on the relative imports.
- When adding a new module, drop it in `src/` and import it as `from .new_module import ...`.

## Architecture

The CLI is a thin shell over several cooperating layers. Understanding the flow from
`main` → path resolution → mod DB → mod manager is the fastest way to get oriented.
Two cross-cutting infra modules (`pager`, `colors`) wrap the print-time concerns so the
rest of the code stays plain `print()`.

### Entry + global options (`main.py`)

`main()` hand-parses global flags (`--celeste-dir`, `--log-level`) before dispatching to
`CelesteModCLI`. This is intentional: subcommand parsing uses `optparse` per-subcommand,
so global flags must be stripped first. The two side effects that happen *before*
dispatch and that every subcommand depends on:

1. `loguru` is reconfigured to the requested log level (default `ERROR`).
2. `set_mod_paths(celeste_dir)` populates the **mutable globals** `config.MODS_DIR`,
   `config.MOD_DB_PATH`, and `config.BLACKLIST_PATH`. Nothing in `mod_manager` /
   `mod_db` / `mod` / `blacklist` works until this has run — they all read those
   globals at call time.

### Celeste path discovery (`path.py` + `steam.py`)

Auto-detection walks Steam's `libraryfolders.vdf` for app id `504230`, then resolves the
`installdir` from the per-app `appmanifest_<id>.acf`. Platform handling:

- Windows / Linux: the Steam library folder is the Celeste root.
- macOS: append `Celeste.app/Contents/Resources` because mods live inside the bundle.

`--celeste-dir` overrides discovery entirely. Steam-root lookup is platform-specific
(registry on Windows, well-known paths on macOS/Linux); the Windows branch is
explicitly noted as untested.

### Mod database (`mod_db.py`)

Pulls the full mod list JSON from `https://celeste.weg.fan/api/v2/mod/list` and caches
it as `<celeste_dir>/celeste_mod_db.json`. The cache is refreshed when older than
`DB_UPDATE_PERIOD_DAYS` (7) — measured by a `lastUpdateTime` field this code injects
into the cached JSON, *not* by file mtime. `ModInfo` / `ModSubmissionFile` /
`ModSubmission` are nested dataclasses built via `from_dict` chaining.

### Local mods + dependency resolution (`mod_manager.py`, `mod.py`)

A "local mod" is a `<Name>-<Version>.zip` either directly in `Mods/` or one level deep
in a subdirectory (the `Cache` subdir is skipped). `Mod.from_filename` parses the name
and version from that filename — it does **not** read `everest.yaml` for identity (see
the `REVIEW` comment in `mod.py`); the YAML is only loaded on demand to read
`Dependencies` / `OptionalDependencies`.

`resolve_deps` does a recursive DFS with a `_visited` set of mod names to break cycles.
Three names are hard-coded as core components and skipped: `Everest`, `Celeste`,
`EverestCore`. Version mismatches between the requested dep and what is installed (or
what the registry returns) are logged as warnings but never block installation.

`analyse_mod_deps` (used by `list-tree`) builds a dependency graph from the *installed*
set, runs a cycle check that calls `sys.exit(1)` on detection, then prints from the
roots (in-degree 0 considering only required edges). Missing deps are rendered in red;
optional edges are tagged `(Optional)`.

`ensure_mod` returns `(Mod | None, EnsureModStatus)` — callers must branch on the enum
to distinguish "already there" from "freshly installed" from the various failure modes.
The CLI's per-mod print logic in `cli.py:_install_mod` is the canonical example.

### Enable / disable + blacklist (`blacklist.py`, `mod_manager.py`)

Mods are toggled by maintaining `<MODS_DIR>/blacklist.txt` — Everest skips any line
listed there at load time. `read_blacklist()` returns `(comment_lines, active_entries)`
and `write_blacklist(...)` rewrites preserving the comment header so user annotations
aren't clobbered. `partition_installed_mods()` joins this with the on-disk scan and
returns `(enabled, disabled)`.

The `enable` / `disable` CLI commands compute a **dependency closure** before writing
the blacklist:

- `disable_closure(selected, enabled)` — reverse-walks the required-dep graph among
  *currently enabled* mods. Disabling A also disables anything that requires A
  (transitively), so no enabled mod is left with a missing required dep.
- `enable_closure(selected, disabled, enabled)` — forward-walks each selection's
  required deps. Anything in the *currently disabled* set is added (recursively).
  Required deps that aren't installed *at all* are returned in a `missing` list —
  the CLI surfaces these as warnings rather than auto-installing (that belongs to
  `install`).

Both closures consider **required** deps only. Optional deps are deliberately not
pulled along by either operation.

`blacklist_key(mod)` is the relative-to-`Mods/` filename used as the blacklist line.
For the common case of top-level zips it is just `Name-Version.zip`.

### Output infrastructure (`pager.py`, `colors.py`)

Two cross-cutting concerns wrapped in tiny modules so call sites can keep using plain
`print()`:

- **`pager.paged_output()`** — context manager that swaps `sys.stdout` for a
  `StringIO`, then pipes the captured text through `$PAGER` (or `less -R -F -X`, then
  `more`, then plain print) on exit. Wraps `search` / `list` / `list-tree` so large
  output is browsable. Pipes / redirects bypass it (TTY check). `install` and the
  toggle commands are deliberately *not* paginated — they're actions with running
  feedback or interactive prompts, not previews.
- **`colors.color(text, *styles)`** — wraps text in ANSI codes when `$NO_COLOR` is
  unset *and* `sys.__stdout__` is a TTY. The check uses `sys.__stdout__` (not
  `sys.stdout`) on purpose so it's still correct while `paged_output` has redirected
  `sys.stdout` to a `StringIO`. `less -R` then renders the colors when paging; pipes
  still see no escape bytes.

Both are transparent to callers — `pretty_print_mod_info`, `pretty_print_mods`, and
`analyse_mod_deps` use plain `print()` and the helpers handle TTY/no-color correctness.

## Conventions worth preserving

- All user-facing errors go to `stderr` via `print(..., file=sys.stderr)`; diagnostics
  go through `loguru`. Don't mix the two channels.
- Subcommand help text is written by hand inside each method (`textwrap.dedent`) rather
  than relying on `optparse`'s generated usage. Match that style when adding commands.
- `config.py` holds module-level constants *and* three paths (`MOD_DB_PATH`,
  `MODS_DIR`, `BLACKLIST_PATH`) that are mutated at startup. Treat them as
  initialized-once globals; don't reassign them outside `set_mod_paths`.
- Reach for `pager.paged_output()` only for *browse* commands (read-only, large
  output). Action commands (`install`, `enable`, `disable`) should print progress
  directly so the user sees what's happening as it happens.
- Never embed raw `\033[...]` ANSI escapes — use `colors.color(text, *styles)` so
  `NO_COLOR` and TTY detection work uniformly. The one historical inline red in
  `cli.py` was migrated when colors landed.
- Interactive multi-select / confirm goes through `questionary`. The `_ask_checkbox`
  helper in `cli.py` is the wrapper — it returns `None` on Ctrl-C, and callers must
  handle that as "abort, do nothing".
