import sqlite3
from contextlib import closing

import pytest

from itrampoline.database import REQUIRED_COLUMNS, patch_database
from itrampoline.errors import TrampolineError
from itrampoline.stock import DEFAULT_SLOT, SLOTS, TITLE


def test_entry_preserves_existing_games_links_counters_and_other_tables(catalogue: bytes) -> None:
    patched = patch_database(catalogue, DEFAULT_SLOT)

    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(patched)
        first, existing = database.execute("SELECT * FROM tbl_game").fetchall()

        assert first[1:3] == (TITLE, ".itr")
        assert first[7:9] == (2, 2)
        assert first[-1] == "/sdcard/game"
        assert existing == (42, "Existing game", ".nes", 8, 6, 7, 2, 1, 1, 0, "/sdcard/game/fc")
        assert database.execute("SELECT * FROM tbl_total").fetchall() == [(1, 2)]
        assert database.execute("SELECT * FROM unrelated").fetchall() == [("do not touch",)]

        for table, key, title, linked_id in (
            ("tbl_en", "en_id", "en_title", first[4]),
            ("tbl_tw", "en_id", "en_title", first[4]),
            ("tbl_zh", "zh_id", "zh_title", first[3]),
            ("tbl_ko", "ko_id", "ko_title", first[5]),
        ):
            assert database.execute(
                f"SELECT {title} FROM {table} WHERE {key} = ?", (linked_id,),
            ).fetchall() == [(TITLE,)]
            assert database.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (2,)

    assert patch_database(patched, DEFAULT_SLOT) == patched


def test_reselecting_slot_updates_one_row_without_duplicate_titles(catalogue: bytes) -> None:
    first = patch_database(catalogue, DEFAULT_SLOT)
    second = patch_database(first, SLOTS[-1])

    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(second)
        selected = database.execute(
            "SELECT game_type, class_type FROM tbl_game WHERE game = ?", (TITLE,),
        ).fetchall()

        assert selected == [(1, 1)]
        assert database.execute("SELECT total FROM tbl_total").fetchone() == (2,)
        assert database.execute("SELECT COUNT(*) FROM tbl_en").fetchone() == (2,)


@pytest.mark.parametrize("table", REQUIRED_COLUMNS)
def test_missing_table_is_rejected_before_database_is_changed(catalogue: bytes, table: str) -> None:

    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(catalogue)
        database.execute(f"DROP TABLE {table}")
        source = database.serialize()

    with pytest.raises(TrampolineError, match=table):
        patch_database(source, DEFAULT_SLOT)


@pytest.mark.parametrize("source", [b"", b"not sqlite", b"SQLite format 3\x00invalid"])
def test_invalid_database_is_not_recreated(source: bytes) -> None:

    with pytest.raises(TrampolineError, match="SQLite|games.db"):
        patch_database(source, DEFAULT_SLOT)


@pytest.mark.parametrize("sql, message", [
    ("ALTER TABLE tbl_game RENAME COLUMN suffix TO unknown", "columns"),
    ("UPDATE tbl_total SET ID = 2", "tbl_total"),
    ("CREATE TRIGGER surprise AFTER INSERT ON tbl_game BEGIN DELETE FROM tbl_en; END", "triggers"),
    ("DROP TABLE tbl_en; CREATE TABLE tbl_en (en_id INTEGER PRIMARY KEY, en_title TEXT)", "rowid alias"),
    ("UPDATE tbl_game SET gameid = 2147483647", "exhausted"),
    ("UPDATE tbl_game SET game = 'iboxed trampoline'", "different game"),
])
def test_unsupported_schema_and_conflicting_ids_are_refused(catalogue: bytes, sql: str, message: str) -> None:

    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(catalogue)
        database.executescript(sql)
        database.commit()
        source = database.serialize()

    with pytest.raises(TrampolineError, match=message):
        patch_database(source, DEFAULT_SLOT)
