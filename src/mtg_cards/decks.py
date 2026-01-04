from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from mtg_cards.db import transaction
from mtg_cards.deck import DeckEntry, parse_decklist


@dataclass(frozen=True)
class DeckSummary:
    id: int
    name: str
    updated_at: str


def list_decks(conn) -> list[DeckSummary]:
    rows = conn.execute(
        "SELECT id, name, updated_at FROM decks ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    return [DeckSummary(id=int(r["id"]), name=str(r["name"]), updated_at=str(r["updated_at"])) for r in rows]


def get_deck(conn, deck_id: int) -> dict:
    row = conn.execute(
        "SELECT id, name, deck_text, created_at, updated_at FROM decks WHERE id = ?",
        (deck_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Deck not found: {deck_id}")
    return dict(row)


def create_deck(conn, *, name: str, deck_text: str) -> int:
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO decks(name, deck_text, created_at, updated_at) VALUES(?, ?, ?, ?)",
            (name.strip() or "New Deck", deck_text, now, now),
        )
        deck_id = int(cur.lastrowid)
        _refresh_deck_cards(conn, deck_id=deck_id, deck_text=deck_text)
    return deck_id


def update_deck(conn, *, deck_id: int, name: str, deck_text: str) -> None:
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(conn):
        conn.execute(
            "UPDATE decks SET name = ?, deck_text = ?, updated_at = ? WHERE id = ?",
            (name.strip() or "Deck", deck_text, now, deck_id),
        )
        _refresh_deck_cards(conn, deck_id=deck_id, deck_text=deck_text)


def delete_deck(conn, *, deck_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))


def _refresh_deck_cards(conn, *, deck_id: int, deck_text: str) -> None:
    entries = parse_decklist(deck_text)
    merged = _merge_entries(entries)

    conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
    if not merged:
        return

    conn.executemany(
        """
        INSERT INTO deck_cards(deck_id, name, set_code, collector_number, quantity)
        VALUES(?, ?, ?, ?, ?)
        """,
        [
            (
                deck_id,
                e.name,
                e.set_code,
                e.collector_number,
                e.qty,
            )
            for e in merged
        ],
    )


def _merge_entries(entries: list[DeckEntry]) -> list[DeckEntry]:
    # Merge duplicates to keep deck_cards compact.
    agg: dict[tuple[str, str | None, str | None], int] = {}
    for e in entries:
        key = (e.name.strip(), e.set_code, e.collector_number)
        if not key[0] or e.qty <= 0:
            continue
        agg[key] = agg.get(key, 0) + int(e.qty)

    out: list[DeckEntry] = []
    for (name, set_code, collector), qty in sorted(agg.items(), key=lambda k: (k[0][0].casefold(), k[0][1] or "", k[0][2] or "")):
        out.append(DeckEntry(qty=qty, name=name, set_code=set_code, collector_number=collector))
    return out
