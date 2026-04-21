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

The CLI is a thin shell over four cooperating layers. Understanding the flow from
`main` → path resolution → mod DB → mod manager is the fastest way to get oriented.

### Entry + global options (`main.py`)

`main()` hand-parses global flags (`--celeste-dir`, `--log-level`) before dispatching to
`CelesteModCLI`. This is intentional: subcommand parsing uses `optparse` per-subcommand,
so global flags must be stripped first. The two side effects that happen *before*
dispatch and that every subcommand depends on:

1. `loguru` is reconfigured to the requested log level (default `ERROR`).
2. `set_mod_paths(celeste_dir)` populates the **mutable globals** `config.MODS_DIR` and
   `config.MOD_DB_PATH`. Nothing in `mod_manager` / `mod_db` / `mod` works until this
   has run — they all read those globals at call time.

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

## Conventions worth preserving

- All user-facing errors go to `stderr` via `print(..., file=sys.stderr)`; diagnostics
  go through `loguru`. Don't mix the two channels.
- Subcommand help text is written by hand inside each method (`textwrap.dedent`) rather
  than relying on `optparse`'s generated usage. Match that style when adding commands.
- `config.py` holds module-level constants *and* two paths (`MOD_DB_PATH`, `MODS_DIR`)
  that are mutated at startup. Treat them as initialized-once globals; don't reassign
  them outside `set_mod_paths`.
