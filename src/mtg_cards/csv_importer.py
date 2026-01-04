from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mtg_cards.db import transaction
from mtg_cards.scryfall import sha256_file


@dataclass(frozen=True)
class ImportResult:
    file: Path
    skipped_already_imported: bool
    rows_seen: int
    rows_imported: int
    rows_unmatched: int
    quantity_added: int
    distinct_printings_matched: int


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _first_present(row: dict[str, str], keys: list[str]) -> str:
    lowered = {k.lower(): v for k, v in row.items()}
    for k in keys:
        if k.lower() in lowered:
            v = lowered[k.lower()]
            if v is not None:
                v = str(v).strip()
            if v:
                return v
    return ""


def _parse_quantity(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def import_csv_file(conn, csv_path: Path) -> ImportResult:
    file_hash = sha256_file(csv_path)

    already = conn.execute("SELECT 1 FROM import_files WHERE file_hash = ?", (file_hash,)).fetchone()
    if already is not None:
        return ImportResult(
            file=csv_path,
            skipped_already_imported=True,
            rows_seen=0,
            rows_imported=0,
            rows_unmatched=0,
            quantity_added=0,
            distinct_printings_matched=0,
        )

    rows_seen = 0
    rows_imported = 0
    rows_unmatched = 0
    quantity_added = 0
    matched: set[str] = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")

        with transaction(conn):
            for row in reader:
                rows_seen += 1

                qty = _parse_quantity(
                    _first_present(row, ["quantity", "qty", "count", "amount", "owned"])
                )
                if qty <= 0:
                    continue

                name = _first_present(row, ["name", "card", "card name", "card_name"])
                if not name:
                    continue

                set_code = _first_present(row, ["set", "set code", "set_code"])
                collector = _first_present(
                    row,
                    [
                        "collector_number",
                        "collector",
                        "collector number",
                        "number",
                        "cn",
                    ],
                )

                scryfall_id = _resolve_printing_scryfall_id(conn, name=name, set_code=set_code, collector=collector)
                if not scryfall_id:
                    rows_unmatched += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO owned_printings(scryfall_id, quantity)
                    VALUES(?, ?)
                    ON CONFLICT(scryfall_id) DO UPDATE SET quantity = quantity + excluded.quantity
                    """,
                    (scryfall_id, qty),
                )
                rows_imported += 1
                quantity_added += qty
                matched.add(str(scryfall_id))

            conn.execute(
                "INSERT INTO import_files(file_hash, filename, imported_at) VALUES(?, ?, ?)",
                (file_hash, csv_path.name, dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
            )

    return ImportResult(
        file=csv_path,
        skipped_already_imported=False,
        rows_seen=rows_seen,
        rows_imported=rows_imported,
        rows_unmatched=rows_unmatched,
        quantity_added=quantity_added,
        distinct_printings_matched=len(matched),
    )


def _resolve_printing_scryfall_id(conn, *, name: str, set_code: str, collector: str) -> str | None:
    name = _norm(name)
    set_code = _norm(set_code).lower()
    collector = _norm(collector)

    # Strong match: name + set + collector
    if set_code and collector:
        row = conn.execute(
            """
            SELECT scryfall_id
            FROM printings
            WHERE lower(name) = lower(?) AND lower(set_code) = ? AND collector_number = ?
            LIMIT 1
            """,
            (name, set_code, collector),
        ).fetchone()
        if row is not None:
            return str(row[0])

    # Next: name + set
    if set_code:
        row = conn.execute(
            """
            SELECT scryfall_id
            FROM printings
            WHERE lower(name) = lower(?) AND lower(set_code) = ?
            ORDER BY released_at DESC
            LIMIT 1
            """,
            (name, set_code),
        ).fetchone()
        if row is not None:
            return str(row[0])

    # Fallback: name only (prefer newest English printing)
    row = conn.execute(
        """
        SELECT scryfall_id
        FROM printings
        WHERE lower(name) = lower(?) AND (lang IS NULL OR lang = 'en') AND is_token = 0 AND is_digital = 0
        ORDER BY released_at DESC
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if row is not None:
        return str(row[0])

    return None
