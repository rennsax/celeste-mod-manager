import os
import sys

_RESET = "\033[0m"
_CODES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
}


def use_color() -> bool:
    """True iff NO_COLOR is unset and the original stdout is a TTY.

    Uses ``sys.__stdout__`` so the result is correct even while ``sys.stdout``
    is temporarily redirected (e.g. inside :func:`pager.paged_output`).
    """
    if os.environ.get("NO_COLOR"):
        return False
    stream = sys.__stdout__
    return stream is not None and stream.isatty()


def color(text: str, *styles: str) -> str:
    """Wrap ``text`` in ANSI styles, or return it unchanged when colors are off."""
    if not styles or not use_color():
        return text
    prefix = "".join(_CODES[s] for s in styles)
    return f"{prefix}{text}{_RESET}"
