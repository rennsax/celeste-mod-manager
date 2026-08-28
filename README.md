# Celeste Mod Manager

A command-line tool for managing Celeste mods.

## Features

- Search the mod database
- Select either the WEGFAN or official GameBanana mod source
- Apply a declarative mod requirement file
- Resolve and download dependencies
- List installed mods
- Display the dependency tree for enabled mods
- Check and apply mod updates
- Garbage-collect disabled mod archives after confirmation

## Project Status

This project is still in an early stage. CLI behavior and options may change.

## Requirements

- Python 3.10+
- Steam version of Celeste

`celeste-mod-manager` tries to detect your Celeste installation path automatically via Steam library information.

## Installation

Clone this repo and execute the following commands at the project root:

```
pip install .
```

Now you can use `celeste-mod-manager` to manage your Celeste mods in the command line. Check `celeste-mod-manager help` for documentation.

## Declarative workflow

An installed mod is any valid mod archive in Celeste's `Mods` directory, whether
it is enabled or disabled. The desired enabled state comes from a requirement
file containing one requested mod name per line. By default, the manager reads
`Mods/required_mods.txt`:

```text
StrawberryJam2021
CelesteTAS
```

Apply that desired state with:

```sh
celeste-mod-manager apply
```

`apply` downloads missing requested mods and dependencies, then rewrites
`blacklist.txt` and `modoptionsorder.txt` to match the requested dependency
closure. To stop using a mod, remove it from the requirement file and run
`apply` again. Its now-unneeded archive remains installed but disabled until you
explicitly delete disabled archives with:

```sh
celeste-mod-manager gc
```

Legacy `Mods/installed_mods.yml` files are ignored and left untouched.

## Mod sources

WEGFAN is selected by default. To use the official GameBanana catalog and file
downloads for a command, place the global option before the command name:

```sh
celeste-mod-manager --mod-source gamebanana search CelesteTAS
celeste-mod-manager --mod-source gamebanana apply
celeste-mod-manager --mod-source gamebanana upgrade ALL
```

Set `config.MOD_SOURCE` to `"wegfan"` or `"gamebanana"` to choose a persistent
default. A command uses only its selected source: a missing catalog entry,
network failure, checksum mismatch, or invalid archive never falls back to the
other source. Existing valid archives in `Mods/` can still be reused after a
source switch.

The normalized catalogs are cached independently as
`Mods/celeste_mod_db.wegfan.json` and
`Mods/celeste_mod_db.gamebanana.json`. The legacy
`Mods/celeste_mod_db.json` file is ignored and left untouched.

## License

MIT.

## Q & A

> Why did you create another mod manager for Celeste? Why not use Olympus or CeleMod?

Well, the most important reason is that I'm very sensitive to what applications do to my system. Both the two mod managers are GUI-based so I cannot determine their behavior. To my knowledge, managing the mods for Celeste is a simple task, so I decide to do it by my own. As my scripts accumulate, I find it may be helpful to combine them into a lightweight application, so that anyone who prefers CLI programs can also benefit from it.
