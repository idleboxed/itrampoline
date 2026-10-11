import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from itrampoline.errors import TrampolineError
from itrampoline.filesystem import FileSnapshot, SDTree
from itrampoline.install import InstallSession, apply_install, prepare_install, read_target
from itrampoline.stock import DATABASE_PATH, DEFAULT_SLOT, HELPER_PATH, TOKEN_PATH


def test_minimal_install_has_pc_recovery_and_idempotent_second_run(sd_tree: Path, tmp_path: Path) -> None:
    original = (sd_tree / DATABASE_PATH).read_bytes()

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"synthetic core", helper=b"synthetic helper")
        backup = apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)
        again = prepare_install(tree, DEFAULT_SLOT, core=b"synthetic core", helper=b"synthetic helper")
        second_backup = apply_install(tree, again, backup_root=tmp_path / "backups", allow_replace=False)

    assert second_backup is None
    assert (backup / "3-games.db").read_bytes() == original
    assert (sd_tree / HELPER_PATH).is_file()
    assert (sd_tree / TOKEN_PATH).read_bytes() == b"system/iman/iman\n"
    assert (sd_tree / "game/database.sqlite3").read_bytes() == b"untouched history"
    assert {path.relative_to(sd_tree).as_posix() for path in sd_tree.rglob("*") if path.is_file()} == {
        DATABASE_PATH, "game/database.sqlite3", HELPER_PATH, TOKEN_PATH, f"retro_lib/{DEFAULT_SLOT.filename}",
    }
    manifest = json.loads((backup / "restore.json").read_text())

    assert len(manifest["files"]) == 4


def test_conflict_without_approval_refuses_before_any_writes(sd_tree: Path, tmp_path: Path) -> None:
    (sd_tree / "retro_lib").mkdir()
    core_path = sd_tree / "retro_lib" / DEFAULT_SLOT.filename
    core_path.write_bytes(b"stock core")
    original = (sd_tree / DATABASE_PATH).read_bytes()

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"new core", helper=b"new helper")

        with pytest.raises(TrampolineError, match="approval"):
            apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)

    assert core_path.read_bytes() == b"stock core"
    assert (sd_tree / DATABASE_PATH).read_bytes() == original
    assert not (sd_tree / "system").exists()
    assert not (tmp_path / "backups").exists()


def test_approved_replacement_preserves_original_core(sd_tree: Path, tmp_path: Path) -> None:
    (sd_tree / "retro_lib").mkdir()
    (sd_tree / "retro_lib" / DEFAULT_SLOT.filename).write_bytes(b"stock core")

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"new core", helper=b"new helper")
        backup = apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=True)

    assert (backup / f"0-{DEFAULT_SLOT.filename}").read_bytes() == b"stock core"


@pytest.mark.parametrize("path, create_destination, message", [
    pytest.param(DATABASE_PATH, Path.touch, "Too many levels of symbolic links", id="database"),
    pytest.param("game", Path.mkdir, "Not a directory", id="game-directory"),
    pytest.param("retro_lib", Path.mkdir, "Not a directory", id="core-directory"),
    pytest.param(HELPER_PATH, Path.touch, "Too many levels of symbolic links", id="helper"),
])
def test_symlink_in_destination_is_refused(
    sd_tree: Path, tmp_path: Path, path: str, create_destination: Callable[..., None], message: str,
) -> None:
    destination = sd_tree / path
    outside = tmp_path / "outside"
    destination.parent.mkdir(exist_ok=True)
    create_destination(destination, exist_ok=True)
    destination.rename(outside)
    destination.symlink_to(outside)

    with SDTree(sd_tree) as tree:

        with pytest.raises(OSError, match=message):
            prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")


def test_hardlinked_database_and_sqlite_sidecar_are_refused(sd_tree: Path, tmp_path: Path) -> None:
    source = sd_tree / DATABASE_PATH
    os.link(source, tmp_path / "other-link")

    with SDTree(sd_tree) as tree:

        with pytest.raises(TrampolineError, match="single-link"):
            prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")

    (tmp_path / "other-link").unlink()
    (sd_tree / (DATABASE_PATH + "-wal")).touch()

    with SDTree(sd_tree) as tree:

        with pytest.raises(TrampolineError, match="sidecar"):
            prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")


@pytest.mark.parametrize("token, message", [
    pytest.param(b"../escape\n", "safe relative path", id="parent"),
    pytest.param(b"/bin/app\n", "safe relative path", id="absolute"),
    pytest.param(b"system//app\n", "safe relative path", id="empty-component"),
    pytest.param(b"app", "expected a relative executable path and one newline", id="missing-newline"),
    pytest.param(b"app\x00\n", "safe relative path", id="nul"),
    pytest.param(b"app\nextra\n", "Invalid newline", id="extra-newline"),
])
def test_unsafe_targets_are_rejected(token: bytes, message: str) -> None:

    with pytest.raises(TrampolineError, match=message):
        read_target(token)


def test_custom_relative_application_path_is_preserved(sd_tree: Path, tmp_path: Path) -> None:
    (sd_tree / TOKEN_PATH).write_bytes(b"system/custom-app\n")

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")
        apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)

    assert (sd_tree / TOKEN_PATH).read_bytes() == b"system/custom-app\n"


def test_changed_root_and_changed_db_fail_before_publication(sd_tree: Path, tmp_path: Path) -> None:

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")
        (sd_tree / DATABASE_PATH).write_bytes(b"external update")

        with pytest.raises(TrampolineError, match="changed"):
            apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)

        sd_tree.rename(tmp_path / "old-root")
        sd_tree.mkdir()

        with pytest.raises(TrampolineError, match="root was replaced"):
            tree.validate_root()


def test_publication_failure_keeps_db_original_and_pc_backup(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (sd_tree / DATABASE_PATH).read_bytes()

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")
        publish = tree.publish

        def fail_second(path: str, content: bytes, mode: int, before: FileSnapshot | None) -> None:

            if path == HELPER_PATH:
                raise OSError("Synthetic interruption")

            publish(path, content, mode, before)

        monkeypatch.setattr(tree, "publish", fail_second)

        with pytest.raises(OSError, match="Synthetic interruption"):
            apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)

    assert (sd_tree / DATABASE_PATH).read_bytes() == original
    assert (sd_tree / "retro_lib" / DEFAULT_SLOT.filename).read_bytes() == b"core"
    assert list((tmp_path / "backups").glob("*/3-games.db"))[0].read_bytes() == original
    assert not list(sd_tree.rglob(".itrampoline-*"))


def test_insufficient_space_and_unsafe_backup_fail_before_publication(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")

        with monkeypatch.context() as patch:
            patch.setattr(
                "itrampoline.install.os.fstatvfs",
                lambda descriptor: os.statvfs_result((4096, 4096, 0, 0, 0, 0, 0, 0, 0, 255)),
            )

            with pytest.raises(TrampolineError, match="free space"):
                apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)

        with pytest.raises(TrampolineError, match="outside"):
            apply_install(tree, plan, backup_root=sd_tree / "backups", allow_replace=False)

    assert not (sd_tree / "system").exists()
    assert not (tmp_path / "backups").exists()


def test_cancel_during_staging_removes_temporary_file_and_keeps_recovery(
    sd_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (sd_tree / DATABASE_PATH).read_bytes()

    with SDTree(sd_tree) as tree:
        plan = prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")

        def cancelled_write(descriptor: int, content: object) -> int:
            raise KeyboardInterrupt("Synthetic cancellation")

        monkeypatch.setattr("itrampoline.filesystem.os.write", cancelled_write)

        with pytest.raises(KeyboardInterrupt, match="Synthetic cancellation"):
            apply_install(tree, plan, backup_root=tmp_path / "backups", allow_replace=False)

    assert (sd_tree / DATABASE_PATH).read_bytes() == original
    assert not list(sd_tree.rglob(".itrampoline-*"))
    assert list((tmp_path / "backups").glob("*/restore.json"))


def test_nested_mount_is_refused_without_reading_target(
    sd_tree: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (sd_tree / "retro_lib").mkdir()
    real_stat = os.fstat

    with SDTree(sd_tree) as tree:

        def changed_device(descriptor: int) -> os.stat_result:
            metadata = real_stat(descriptor)

            if metadata.st_ino == (sd_tree / "retro_lib").stat().st_ino:
                values = list(metadata)
                values[2] += 1
                return os.stat_result(values)

            return metadata

        monkeypatch.setattr("itrampoline.filesystem.os.fstat", changed_device)

        with pytest.raises(TrampolineError, match="nested filesystem"):
            prepare_install(tree, DEFAULT_SLOT, core=b"core", helper=b"helper")


def test_session_construction_does_not_acquire_resources(tmp_path: Path, forbidden_build: None) -> None:
    root = tmp_path / "missing-sd"

    session = InstallSession(root, DEFAULT_SLOT)

    assert not root.exists()

    with pytest.raises(RuntimeError, match="not open"):
        session.get_conflicts()

    with pytest.raises(RuntimeError, match="not open"):
        session.apply(backup_root=tmp_path / "backups", allow_replace=False)

    assert not (tmp_path / "backups").exists()


def test_incompatible_catalogue_fails_before_build_and_releases_root(
    sd_tree: Path, catalogue: bytes, forbidden_build: None,
) -> None:

    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(catalogue)
        database.execute("DROP TABLE tbl_game")
        source = database.serialize()

    (sd_tree / DATABASE_PATH).write_bytes(source)

    with pytest.raises(TrampolineError, match="table: tbl_game"):

        with InstallSession(sd_tree, DEFAULT_SLOT):
            pytest.fail("An incompatible catalogue must prevent entering the session")

    assert (sd_tree / DATABASE_PATH).read_bytes() == source
    assert not (sd_tree / "system").exists()

    with SDTree(sd_tree) as tree:
        tree.validate_root()


@pytest.mark.parametrize("interruption", [OSError, KeyboardInterrupt])
def test_build_interruption_releases_root_and_temporary_directory(
    sd_tree: Path, monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException],
) -> None:
    original = (sd_tree / DATABASE_PATH).read_bytes()
    directories: list[Path] = []

    def interrupted_build(directory: Path) -> tuple[bytes, bytes]:
        directories.append(directory)
        raise interruption("Synthetic build interruption")

    monkeypatch.setattr("itrampoline.install.build_runtime", interrupted_build)

    with pytest.raises(interruption, match="Synthetic build interruption"):

        with InstallSession(sd_tree, DEFAULT_SLOT):
            pytest.fail("An interrupted build must prevent entering the session")

    assert directories and all(not directory.exists() for directory in directories)
    assert (sd_tree / DATABASE_PATH).read_bytes() == original
    assert not (sd_tree / "system").exists()

    with SDTree(sd_tree) as tree:
        tree.validate_root()
