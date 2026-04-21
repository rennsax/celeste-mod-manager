import io
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from typing import Iterator


def paginate(text: str) -> None:
    """Pipe ``text`` through a pager when stdout is a TTY; print as-is otherwise."""
    if not text:
        return
    if not sys.stdout.isatty():
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return

    pager_env = os.environ.get("PAGER")
    if pager_env:
        cmd: list[str] | str = pager_env
        shell = True
    elif shutil.which("less"):
        # -R: keep ANSI colors, -F: skip pager if one screen, -X: don't clear screen
        cmd = ["less", "-R", "-F", "-X"]
        shell = False
    elif shutil.which("more"):
        cmd = ["more"]
        shell = False
    else:
        sys.stdout.write(text)
        return

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, shell=shell)
    except OSError:
        sys.stdout.write(text)
        return
    try:
        proc.communicate(text.encode("utf-8", errors="replace"))
    except (BrokenPipeError, KeyboardInterrupt):
        try:
            proc.terminate()
        except OSError:
            pass


@contextmanager
def paged_output() -> Iterator[io.StringIO]:
    """Capture stdout writes inside the block and pipe them through a pager on exit."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = saved
        paginate(buf.getvalue())
