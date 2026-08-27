import sys
from pathlib import Path

import pytest

from src import config, main, mod_db, path as celeste_path
from src.cli import CelesteModCLI


def _prepare_main(monkeypatch, tmp_path: Path, *args: str) -> None:
    (tmp_path / "Celeste.exe").touch(exist_ok=True)
    (tmp_path / "Mods").mkdir(exist_ok=True)
    monkeypatch.setattr(config, "CELESTE_DIR", "")
    monkeypatch.setattr(celeste_path, "find_celeste_dir_from_steam", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", *args])


def test_help_lists_apply_as_a_regular_command(capsys):
    main.cmd_help()

    captured = capsys.readouterr()
    assert "    apply              Apply the desired mod state" in captured.err
    assert "Experimental commands:" not in captured.err
    for command in ("install", "uninstall", "enable", "disable"):
        assert f"    {command} " not in captured.err


@pytest.mark.parametrize("command", ["install", "uninstall", "enable", "disable"])
def test_removed_commands_are_unknown(
    command: str, tmp_path: Path, monkeypatch, capsys
):
    _prepare_main(monkeypatch, tmp_path, command)

    assert main.main() == 1

    captured = capsys.readouterr()
    assert captured.out == "\n"
    assert f"ERROR: unknown command '{command}'\n" in captured.err


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


def test_search_reports_database_failure_without_internal_error(monkeypatch, capsys):
    monkeypatch.setattr(
        mod_db,
        "search_mod_by_name",
        lambda _pattern: (_ for _ in ()).throw(OSError("database is read-only")),
    )

    assert CelesteModCLI().search(["CelesteTAS"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: failed to load the local mod database: database is read-only\n"
    )


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


def test_missing_mods_directory_fails_before_non_everest_dispatch(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "Celeste.exe").touch()
    monkeypatch.setattr(config, "CELESTE_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "CelesteModCLI",
        lambda: pytest.fail("missing Mods must fail before dispatch"),
    )
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", "list"])

    assert main.main() == 1
    assert capsys.readouterr().err == (
        f"ERROR: Mods directory '{tmp_path / 'Mods'}' does not exist. "
        "Everest may not be installed or may be damaged. Install or repair it "
        "with 'celeste-mod-manager everest'.\n"
    )


def test_everest_dispatch_allows_valid_celeste_without_mods(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "Celeste.exe").touch()
    monkeypatch.setattr(config, "CELESTE_DIR", str(tmp_path))
    calls = []

    class FakeCLI:
        def everest(self, args, prog_name):
            calls.append((args, prog_name))
            return 0

    monkeypatch.setattr(main, "CelesteModCLI", FakeCLI)
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", "everest"])

    assert main.main() == 0
    assert calls == [([], "celeste-mod-manager everest")]
    assert capsys.readouterr().err == ""


def test_empty_mods_directory_remains_a_valid_empty_install(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "Celeste.exe").touch()
    (tmp_path / "Mods").mkdir()
    monkeypatch.setattr(config, "CELESTE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", "list"])

    assert main.main() == 0
    assert capsys.readouterr().out == "No mods installed.\n"


def test_database_directory_fails_before_database_command_dispatch(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "Celeste.exe").touch()
    mods_dir = tmp_path / "Mods"
    mods_dir.mkdir()
    (mods_dir / "celeste_mod_db.json").mkdir()
    monkeypatch.setattr(config, "CELESTE_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "CelesteModCLI",
        lambda: pytest.fail("invalid database path must fail before dispatch"),
    )
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", "search", "CelesteTAS"])

    assert main.main() == 1
    assert "expected a file, found a directory" in capsys.readouterr().err


def test_cli_celeste_dir_overrides_configured_path(tmp_path: Path, monkeypatch, capsys):
    configured = tmp_path / "configured"
    override = tmp_path / "override"
    for celeste_dir in (configured, override):
        celeste_dir.mkdir()
        (celeste_dir / "Celeste.exe").touch()
        (celeste_dir / "Mods").mkdir()

    monkeypatch.setattr(config, "CELESTE_DIR", str(configured))
    monkeypatch.setattr(
        celeste_path,
        "find_celeste_dir_from_steam",
        lambda: pytest.fail("CLI override must not use automatic discovery"),
    )
    calls = []

    class FakeCLI:
        def list_mods(self, args):
            calls.append((args, config.CELESTE_DIR))
            return 0

    monkeypatch.setattr(main, "CelesteModCLI", FakeCLI)
    monkeypatch.setattr(
        sys,
        "argv",
        ["celeste-mod-manager", "--celeste-dir", str(override), "list"],
    )

    assert main.main() == 0
    assert calls == [([], str(override.resolve()))]
    assert capsys.readouterr().err == ""


def test_unknown_command_does_not_trigger_path_resolution(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "configure_celeste_dir",
        lambda override: pytest.fail("unknown command must not resolve paths"),
    )
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", "unknown"])

    assert main.main() == 1
    assert "ERROR: unknown command 'unknown'" in capsys.readouterr().err


def test_subcommand_help_does_not_trigger_path_resolution(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "configure_celeste_dir",
        lambda override: pytest.fail("subcommand help must not resolve paths"),
    )
    monkeypatch.setattr(sys, "argv", ["celeste-mod-manager", "apply", "--help"])

    assert main.main() == 0
    assert "Usage:" in capsys.readouterr().out
