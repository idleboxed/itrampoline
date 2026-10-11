from pathlib import Path

import pytest

from itrampoline.cli import main
from itrampoline.filesystem import SDTree
from itrampoline.stock import DEFAULT_SLOT


@pytest.mark.parametrize("arguments, message", [
    pytest.param([], "required", id="missing-sd-path"),
    pytest.param(["/tmp", "--so-name", "custom.so"], "unrecognized arguments", id="unapproved-option"),
    pytest.param(["/tmp", "--no"], "unrecognized arguments", id="abbreviated-option"),
])
def test_invalid_cli_arguments_show_usage(
    arguments: list[str], message: str, capsys: pytest.CaptureFixture[str],
) -> None:

    with pytest.raises(SystemExit, match="2") as error:
        main(arguments)

    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_no_ask_never_reads_input(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_build: list[Path],
) -> None:
    monkeypatch.chdir(tmp_path)

    def forbidden_input() -> str:
        raise AssertionError("--no-ask must not ask")

    monkeypatch.setattr("builtins.input", forbidden_input)

    result = main([f"{sd_tree}", "--no-ask"])

    assert result == 0
    assert (sd_tree / "retro_lib" / DEFAULT_SLOT.filename).is_file()
    assert runtime_build and all(not directory.exists() for directory in runtime_build)


def test_interactive_choice_and_replacement_confirmation(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_build: list[Path],
) -> None:
    monkeypatch.chdir(tmp_path)
    (sd_tree / "retro_lib").mkdir()
    (sd_tree / "retro_lib/prosystem_libretro.so").write_bytes(b"stock")
    answers = iter(["2", "y"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    result = main([f"{sd_tree}"])

    assert result == 0
    assert (sd_tree / "retro_lib/prosystem_libretro.so").read_bytes() == b"core"
    assert runtime_build and all(not directory.exists() for directory in runtime_build)


def test_invalid_selection_does_not_build_or_write(
    sd_tree: Path, monkeypatch: pytest.MonkeyPatch, forbidden_build: None, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("builtins.input", lambda: "1000")

    result = main([f"{sd_tree}"])

    assert result == 1
    assert "Invalid core number" in caplog.text
    assert not (sd_tree / "system").exists()


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_cancel_at_core_choice_does_not_build_or_write(
    sd_tree: Path, monkeypatch: pytest.MonkeyPatch, forbidden_build: None, interruption: type[BaseException],
) -> None:
    def cancelled_input() -> str:
        raise interruption("Synthetic cancellation")

    monkeypatch.setattr("builtins.input", cancelled_input)

    result = main([f"{sd_tree}"])

    assert result == 130
    assert not (sd_tree / "system").exists()


def test_missing_db_fails_before_build(
    tmp_path: Path, forbidden_build: None, caplog: pytest.LogCaptureFixture,
) -> None:
    result = main([f"{tmp_path}", "--no-ask"])

    assert result == 1
    assert "Missing game/games.db" in caplog.text
    assert not (tmp_path / "game/games.db").exists()


@pytest.mark.parametrize("flags, answers", [
    pytest.param(["--no-ask"], [], id="no-ask"),
    pytest.param([], ["1", "n"], id="replacement-declined"),
])
def test_conflict_without_approval_does_not_write_or_create_backups(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_build: list[Path],
    flags: list[str], answers: list[str], caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    (sd_tree / "retro_lib").mkdir()
    core = sd_tree / "retro_lib" / DEFAULT_SLOT.filename
    core.write_bytes(b"stock core")
    original = (sd_tree / "game/games.db").read_bytes()
    responses = iter(answers)
    monkeypatch.setattr("builtins.input", lambda: next(responses))

    result = main([f"{sd_tree}", *flags])

    assert result == 1
    assert "approval" in caplog.text
    assert core.read_bytes() == b"stock core"
    assert (sd_tree / "game/games.db").read_bytes() == original
    assert not (sd_tree / "system").exists()
    assert not (tmp_path / "itrampoline-backups").exists()
    assert runtime_build and all(not directory.exists() for directory in runtime_build)

    with SDTree(sd_tree) as tree:
        tree.validate_root()


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_cancel_at_replacement_confirmation_cleans_resources_without_writing(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_build: list[Path],
    interruption: type[BaseException],
) -> None:
    monkeypatch.chdir(tmp_path)
    (sd_tree / "retro_lib").mkdir()
    core = sd_tree / "retro_lib" / DEFAULT_SLOT.filename
    core.write_bytes(b"stock core")
    original = (sd_tree / "game/games.db").read_bytes()
    answers = iter(["1"])

    def cancelled_confirmation() -> str:
        try:
            return next(answers)

        except StopIteration:
            raise interruption("Synthetic cancellation") from None

    monkeypatch.setattr("builtins.input", cancelled_confirmation)

    result = main([f"{sd_tree}"])

    assert result == 130
    assert core.read_bytes() == b"stock core"
    assert (sd_tree / "game/games.db").read_bytes() == original
    assert not (sd_tree / "system").exists()
    assert not (tmp_path / "itrampoline-backups").exists()
    assert runtime_build and all(not directory.exists() for directory in runtime_build)

    with SDTree(sd_tree) as tree:
        tree.validate_root()
