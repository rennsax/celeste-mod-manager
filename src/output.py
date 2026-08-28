import os
import sys
from typing import TextIO

_ANSI_RESET = "\033[0m"
_ANSI_BOLD_BRIGHT_RED = "\033[1;91m"
_ANSI_BOLD_BRIGHT_YELLOW = "\033[1;93m"


def _supports_color(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ:
        return False
    try:
        return stream.isatty()
    except (AttributeError, OSError):
        return False


def _print_styled_line(
    text: str,
    color: str,
    *,
    file: TextIO | None = None,
) -> None:
    stream = file if file is not None else sys.stderr
    if _supports_color(stream):
        text = f"{color}{text}{_ANSI_RESET}"
    print(text, file=stream)


def print_warning(message: str, *, file: TextIO | None = None) -> None:
    _print_styled_line(
        f"WARNING: {message}",
        _ANSI_BOLD_BRIGHT_YELLOW,
        file=file,
    )


def print_error(message: str, *, file: TextIO | None = None) -> None:
    _print_styled_line(
        f"ERROR: {message}",
        _ANSI_BOLD_BRIGHT_RED,
        file=file,
    )


def print_warning_line(message: str, *, file: TextIO | None = None) -> None:
    """Print a warning whose caller already owns its table-style label."""
    _print_styled_line(message, _ANSI_BOLD_BRIGHT_YELLOW, file=file)
