"""Read/write helpers for Everest's ``Mods/blacklist.txt``.

A blacklisted line is a filename inside ``Mods/`` that Everest will skip when
loading. Lines beginning with ``#`` and blank lines are comments/whitespace.
"""

import os

from . import config

_DEFAULT_HEADER_LINES = [
    "# This file is managed by celeste-mod-manager.",
    "# Each line is a path inside Mods/ that Everest should skip when loading.",
    "# Lines starting with '#' are ignored.",
]


def read_blacklist() -> tuple[list[str], set[str]]:
    """Return ``(comment_lines, active_entries)``.

    ``comment_lines`` preserves comment and blank lines from the existing file
    so a rewrite doesn't lose user annotations. ``active_entries`` is the set
    of currently-blacklisted (= disabled) filenames.
    """
    if not config.BLACKLIST_PATH or not os.path.exists(config.BLACKLIST_PATH):
        return (list(_DEFAULT_HEADER_LINES), set())

    comments: list[str] = []
    entries: set[str] = set()
    with open(config.BLACKLIST_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                comments.append(line)
            else:
                entries.add(stripped)
    if not comments:
        comments = list(_DEFAULT_HEADER_LINES)
    return comments, entries


def write_blacklist(comments: list[str], entries: set[str]) -> None:
    if not config.BLACKLIST_PATH:
        raise RuntimeError("BLACKLIST_PATH not configured; call set_mod_paths first.")
    os.makedirs(os.path.dirname(config.BLACKLIST_PATH), exist_ok=True)
    with open(config.BLACKLIST_PATH, "w", encoding="utf-8") as f:
        for c in comments:
            f.write(c + "\n")
        for e in sorted(entries):
            f.write(e + "\n")
