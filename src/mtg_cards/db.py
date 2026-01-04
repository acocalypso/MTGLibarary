from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printings (
  scryfall_id TEXT PRIMARY KEY,
  oracle_id TEXT,
  name TEXT NOT NULL,
  set_code TEXT NOT NULL,
  collector_number TEXT NOT NULL,
  lang TEXT,
  released_at TEXT,
  mana_cost TEXT,
  type_line TEXT,
  oracle_text TEXT,
  colors TEXT,
  color_identity TEXT,
  rarity TEXT,
  image_uris TEXT,
  card_faces TEXT,
  is_token INTEGER NOT NULL DEFAULT 0,
  is_digital INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_printings_name ON printings(name);
CREATE INDEX IF NOT EXISTS idx_printings_set ON printings(set_code);
CREATE INDEX IF NOT EXISTS idx_printings_type ON printings(type_line);

CREATE TABLE IF NOT EXISTS owned_printings (
  scryfall_id TEXT PRIMARY KEY,
  quantity INTEGER NOT NULL,
  FOREIGN KEY (scryfall_id) REFERENCES printings(scryfall_id)
);

CREATE TABLE IF NOT EXISTS import_files (
  file_hash TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    deck_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    set_code TEXT,
    collector_number TEXT,
    quantity INTEGER NOT NULL,
    PRIMARY KEY (deck_id, name, set_code, collector_number),
    FOREIGN KEY (deck_id) REFERENCES decks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_deck_cards_deck ON deck_cards(deck_id);
"""


def connect(db_file: Path) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Avoid indefinite waits when another connection is doing work.
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def json_dumps(value: Any) -> str:
    def _default(o: Any) -> Any:
        # ijson can yield Decimal for numbers depending on backend/config.
        if isinstance(o, Decimal):
            if o.is_nan() or o.is_infinite():
                return str(o)
            if o == o.to_integral_value():
                return int(o)
            return float(o)
        # Last resort: stringify unknown types rather than crashing an import.
        return str(o)

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_default)


def json_loads(text: str | None) -> Any:
    if not text:
        return None
    return json.loads(text)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    # sqlite3 does not support starting a new BEGIN while already in a transaction.
    # Use SAVEPOINT to allow safe nesting (e.g., a DELETE ran before a bulk import).
    sp_name = "sp_mtg_cards"
    use_savepoint = bool(getattr(conn, "in_transaction", False))
    try:
        if use_savepoint:
            conn.execute(f"SAVEPOINT {sp_name}")
        else:
            conn.execute("BEGIN")
        yield
        if use_savepoint:
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        else:
            conn.commit()
    except Exception:
        if use_savepoint:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        else:
            conn.rollback()
        raise
