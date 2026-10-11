import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture
def catalogue() -> bytes:
    """Small synthetic stock schema, not a copy from a game card."""

    with closing(sqlite3.connect(":memory:")) as database:
        database.executescript("""
        CREATE TABLE tbl_game (gameid INTEGER, game CHAR(50), suffix CHAR(5), zh_id INTEGER,
            en_id INTEGER, ko_id INTEGER, video_id INTEGER, class_type INTEGER, game_type INTEGER,
            hard INTEGER, timer CHAR(50));
        CREATE TABLE tbl_en (en_id INTEGER, en_title CHAR(50));
        CREATE TABLE tbl_tw (en_id INTEGER, en_title CHAR(50));
        CREATE TABLE tbl_zh (zh_id INTEGER, zh_title CHAR(50));
        CREATE TABLE tbl_ko (ko_id, ko_title);
        CREATE TABLE tbl_video (video_id TEXT, video TEXT, path_id TEXT);
        CREATE TABLE tbl_path (path_id TEXT, path TEXT);
        CREATE TABLE tbl_match (ID INTEGER, zh_match CHAR(50));
        CREATE TABLE tbl_total (ID INTEGER PRIMARY KEY, total INTEGER);
        INSERT INTO tbl_game VALUES (42, 'Existing game', '.nes', 8, 6, 7, 2, 1, 1, 0, '/sdcard/game/fc');
        INSERT INTO tbl_en VALUES (6, 'Existing game');
        INSERT INTO tbl_tw VALUES (6, 'Traditional title');
        INSERT INTO tbl_zh VALUES (8, 'Chinese title');
        INSERT INTO tbl_ko VALUES (7, 'Korean title');
        INSERT INTO tbl_video VALUES ('2', 'existing.mp4', '3');
        INSERT INTO tbl_path VALUES ('3', '/sdcard/game/');
        INSERT INTO tbl_match VALUES (42, 'Existing match');
        INSERT INTO tbl_total VALUES (1, 1);
        CREATE TABLE unrelated (value TEXT);
        INSERT INTO unrelated VALUES ('do not touch');
        """)
        return database.serialize()


@pytest.fixture
def sd_tree(tmp_path: Path, catalogue: bytes) -> Path:
    root = tmp_path / "synthetic-sd"
    (root / "game").mkdir(parents=True)
    (root / "game" / "games.db").write_bytes(catalogue)
    (root / "game" / "database.sqlite3").write_bytes(b"untouched history")
    return root


@pytest.fixture
def runtime_build(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    directories: list[Path] = []

    def build_synthetic_runtime(directory: Path) -> tuple[bytes, bytes]:
        directories.append(directory)
        return b"core", b"helper"

    monkeypatch.setattr("itrampoline.install.build_runtime", build_synthetic_runtime)
    return directories


@pytest.fixture
def forbidden_build(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse_build(directory: Path) -> tuple[bytes, bytes]:
        raise AssertionError("Must fail before build")

    monkeypatch.setattr("itrampoline.install.build_runtime", refuse_build)
