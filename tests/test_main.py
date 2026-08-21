import sys
from pathlib import Path

import pytest

from src import main, mod_db
from src.cli import CelesteModCLI


def _prepare_main(monkeypatch, tmp_path: Path, *args: str) -> None:
    monkeypatch.setattr(main, "get_celeste_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "set_mod_paths", lambda _path: None)
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", *args])


def test_search_requires_pattern_without_reading_database(monkeypatch, capsys):
    def fail_if_called(_pattern):
        raise AssertionError("search must not read the database without a pattern")

    monkeypatch.setattr(mod_db, "search_mod_by_name", fail_if_called)

    assert CelesteModCLI().search([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: no search pattern specified.\n"


def test_search_returns_success_when_no_mods_match(monkeypatch, capsys):
    monkeypatch.setattr(mod_db, "search_mod_by_name", lambda _pattern: [])

    assert CelesteModCLI().search(["missing"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "No mods found.\n"
    assert captured.err == ""


def test_search_returns_success_and_preserves_result_output(monkeypatch, capsys):
    found_mod = object()
    monkeypatch.setattr(mod_db, "search_mod_by_name", lambda _pattern: [found_mod])
    monkeypatch.setattr(
        mod_db,
        "pretty_print_mod_info",
        lambda mod: print("mod details") if mod is found_mod else None,
    )

    assert CelesteModCLI().search(["found", "ignored"]) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        "Found 1 mod(s) :\n"
        "----------------------------------------\n"
        "mod details\n"
        "----------------------------------------\n"
    )
    assert captured.err == ""


def test_main_propagates_search_exit_code(tmp_path: Path, monkeypatch, capsys):
    _prepare_main(monkeypatch, tmp_path, "search")
    monkeypatch.setattr(
        mod_db,
        "search_mod_by_name",
        lambda _pattern: pytest.fail("database lookup should not run"),
    )

    assert main.main() == 1
    assert capsys.readouterr().err == "ERROR: no search pattern specified.\n"


def test_main_reports_stable_error_for_unexpected_exception(
    tmp_path: Path, monkeypatch, capsys
):
    class FailingCLI:
        def search(self, _args):
            raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(main, "CelesteModCLI", FailingCLI)
    _prepare_main(monkeypatch, tmp_path, "search", "query")

    assert main.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: an unexpected internal error occurred. "
        "Re-run with --log-level DEBUG for details.\n"
    )
    assert "sensitive internal detail" not in captured.err
    assert "Traceback" not in captured.err


def test_main_logs_unexpected_exception_details_at_debug_level(
    tmp_path: Path, monkeypatch, capsys
):
    class FailingCLI:
        def search(self, _args):
            raise RuntimeError("debug-only detail")

    monkeypatch.setattr(main, "CelesteModCLI", FailingCLI)
    _prepare_main(
        monkeypatch,
        tmp_path,
        "--log-level",
        "DEBUG",
        "search",
        "query",
    )

    assert main.main() == 1

    captured = capsys.readouterr()
    assert "Unhandled exception at CLI boundary." in captured.err
    assert "RuntimeError: debug-only detail" in captured.err
    assert "Re-run with --log-level DEBUG for details." in captured.err


def test_main_does_not_convert_system_exit(tmp_path: Path, monkeypatch):
    class ExitingCLI:
        def search(self, _args):
            raise SystemExit(7)

    monkeypatch.setattr(main, "CelesteModCLI", ExitingCLI)
    _prepare_main(monkeypatch, tmp_path, "search", "query")

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 7
