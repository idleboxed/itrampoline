import sqlite3
from contextlib import closing

from .errors import TrampolineError
from .stock import TITLE, CoreSlot


REQUIRED_COLUMNS = {
    "tbl_game": {
        "gameid", "game", "suffix", "zh_id", "en_id", "ko_id", "video_id",
        "class_type", "game_type", "hard", "timer",
    },
    "tbl_en": {"en_id", "en_title"},
    "tbl_tw": {"en_id", "en_title"},
    "tbl_zh": {"zh_id", "zh_title"},
    "tbl_ko": {"ko_id", "ko_title"},
    "tbl_video": {"video_id", "video", "path_id"},
    "tbl_path": {"path_id", "path"},
    "tbl_match": {"ID", "zh_match"},
    "tbl_total": {"ID", "total"},
}


def validate_schema(database: sqlite3.Connection) -> None:
    """Reject stock databases whose tables or identifiers cannot be safely updated."""

    for table, required in REQUIRED_COLUMNS.items():
        schema = database.execute("SELECT type, sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()

        if not schema or schema[0] != "table" or "WITHOUT ROWID" in schema[1].upper():
            raise TrampolineError(f"Missing or unsupported table: {table}")

        metadata = database.execute(f"PRAGMA table_info(\"{table}\")").fetchall()
        columns = {row[1] for row in metadata}

        if not required <= columns or {"rowid", "_rowid_", "oid"} & {name.lower() for name in columns}:
            raise TrampolineError(f"Missing or unsupported columns in {table}")

        if table != "tbl_total" and any(row[2].upper() == "INTEGER" and row[5] for row in metadata):
            raise TrampolineError(f"Unsupported rowid alias in {table}")

        trigger = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (table,),
        ).fetchone()

        if trigger:
            raise TrampolineError(f"Unexpected triggers on {table}")

    totals = database.execute("SELECT total FROM tbl_total WHERE ID = 1").fetchall()

    if len(totals) != 1 or not isinstance(totals[0][0], int):
        raise TrampolineError("Expected one integer tbl_total row with ID = 1")


def allocate_id(database: sqlite3.Connection, table: str, column: str) -> int:
    """Choose the next positive identifier, rejecting invalid or exhausted values."""
    values = database.execute(f"SELECT \"{column}\" FROM \"{table}\"").fetchall()
    maximum = 0

    for (value,) in values:
        text = f"{value}"

        if not text.isascii() or not text.isdecimal() or not 0 < int(text) < 2**31 - 1:
            raise TrampolineError(f"Invalid or exhausted {table}.{column}")

        maximum = max(maximum, int(text))

    return maximum + 1


def insert_first(database: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    """Insert a row before existing rowids without changing their contents."""
    minimum = database.execute(f"SELECT MIN(rowid) FROM \"{table}\"").fetchone()[0]
    rowid = min(0, minimum or 0) - 1

    if rowid <= -(2**63):
        raise TrampolineError(f"No first rowid available in {table}")

    columns = ", ".join(f"\"{column}\"" for column in values)
    placeholders = ", ".join("?" for _ in values)
    database.execute(
        f"INSERT INTO \"{table}\" (rowid, {columns}) VALUES (?, {placeholders})",
        (rowid, *values.values()),
    )


def patch_database(source: bytes, slot: CoreSlot) -> bytes:
    """Change an in-memory copy; never create/open the user's database for writing."""

    if not source.startswith(b"SQLite format 3\x00"):
        raise TrampolineError("games.db is not a SQLite 3 database")

    try:

        with closing(sqlite3.connect(":memory:")) as database:
            database.deserialize(source)
            validate_schema(database)
            existing = database.execute(
                "SELECT gameid, suffix, timer FROM tbl_game WHERE game = ?", (TITLE,),
            ).fetchall()

            if len(existing) > 1:
                raise TrampolineError("Duplicate trampoline entries; resolve them before installation")

            if existing:
                gameid, suffix, directory = existing[0]

                if suffix != ".itr" or directory != "/sdcard/game":
                    raise TrampolineError("A different game already uses the trampoline title")

                previous = database.execute(
                    "SELECT game_type, class_type FROM tbl_game WHERE gameid = ?", (gameid,),
                ).fetchall()

                if previous == [(slot.type_id, slot.type_id)]:
                    return source

                if len(previous) != 1:
                    raise TrampolineError("Ambiguous trampoline gameid")

                database.execute(
                    "UPDATE tbl_game SET game_type = ?, class_type = ? WHERE gameid = ?",
                    (slot.type_id, slot.type_id, gameid),
                )

            else:
                gameid = allocate_id(database, "tbl_game", "gameid")
                english = max(allocate_id(database, "tbl_en", "en_id"), allocate_id(database, "tbl_tw", "en_id"))
                chinese = allocate_id(database, "tbl_zh", "zh_id")
                korean = allocate_id(database, "tbl_ko", "ko_id")
                video = allocate_id(database, "tbl_video", "video_id")
                path = allocate_id(database, "tbl_path", "path_id")
                # Stock game pairs title/game rows by ordinal position, not by
                # the language IDs in tbl_game: prepend in every language table.
                insert_first(database, "tbl_en", {"en_id": english, "en_title": TITLE})
                insert_first(database, "tbl_tw", {"en_id": english, "en_title": TITLE})
                insert_first(database, "tbl_zh", {"zh_id": chinese, "zh_title": TITLE})
                insert_first(database, "tbl_ko", {"ko_id": korean, "ko_title": TITLE})
                insert_first(database, "tbl_match", {"ID": gameid, "zh_match": TITLE})
                insert_first(database, "tbl_path", {"path_id": f"{path}", "path": "/sdcard/game/"})
                insert_first(database, "tbl_video", {"video_id": f"{video}", "video": None, "path_id": f"{path}"})
                insert_first(database, "tbl_game", {
                    "gameid": gameid, "game": TITLE, "suffix": ".itr", "zh_id": chinese,
                    "en_id": english, "ko_id": korean, "video_id": video,
                    "class_type": slot.type_id, "game_type": slot.type_id,
                    "hard": 0, "timer": "/sdcard/game",
                })
                database.execute("UPDATE tbl_total SET total = total + 1 WHERE ID = 1")

            database.commit()
            return database.serialize()

    except sqlite3.Error as error:
        raise TrampolineError(f"Cannot update games.db: {error}") from error
