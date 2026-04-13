# Celeste Mod Manager

A command-line tool for managing Celeste mods.

## Features

- Search the mod database
- Install one or multiple mods
- Install from a requirements file
- Resolve and install dependencies
- List installed mods
- Display dependency tree for installed mods

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

## License

MIT.

## Q & A

> Why did you create another mod manager for Celeste? Why not use Olympus or CeleMod?

Well, the most important reason is that I'm very sensitive to what applications do to my system. Both the two mod managers are GUI-based so I cannot determine their behavior. To my knowledge, managing the mods for Celeste is a simple task, so I decide to do it by my own. As my scripts accumulate, I find it may be helpful to combine them into a lightweight application, so that anyone who prefers CLI programs can also benefit from it.