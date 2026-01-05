from __future__ import annotations

import re
from dataclasses import dataclass


_DECK_LINE_RE = re.compile(
    r"^\s*(?P<qty>\d+)\s+(?P<name>.+?)(?:\s*\((?P<set>[A-Za-z0-9]{2,6})\)\s+(?P<collector>[^\s]+))?\s*$"
)


@dataclass(frozen=True)
class DeckEntry:
    qty: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None


def parse_decklist(text: str) -> list[DeckEntry]:
    entries: list[DeckEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        # Allow inline comments/annotations (e.g. "# !Commander").
        if " #" in line:
            line = line.split(" #", 1)[0].strip()

        m = _DECK_LINE_RE.match(line)
        if not m:
            continue

        qty = int(m.group("qty"))
        name = (m.group("name") or "").strip()
        set_code = m.group("set")
        collector = m.group("collector")

        if name and qty > 0:
            entries.append(
                DeckEntry(
                    qty=qty,
                    name=name,
                    set_code=set_code.upper() if set_code else None,
                    collector_number=collector if collector else None,
                )
            )

    return entries


@dataclass(frozen=True)
class ValidationRow:
    requested_qty: int
    owned_qty: int
    name: str
    set_code: str | None
    collector_number: str | None

    @property
    def status(self) -> str:
        if self.owned_qty >= self.requested_qty:
            return "Available"
        if self.owned_qty == 0:
            return "Missing"
        return "Partial"


def validate_deck(conn, entries: list[DeckEntry], *, on_status=None) -> list[ValidationRow]:
    # Avoid one DB query per line. For typical decklists (name-only lines),
    # do a single query to get owned quantities per name.
    rows: list[ValidationRow] = []

    if on_status is not None:
        on_status(f"Deck validation: preparing {len(entries)} entries")

    exact: list[DeckEntry] = []
    name_only: list[DeckEntry] = []
    for e in entries:
        if e.set_code and e.collector_number:
            exact.append(e)
        else:
            name_only.append(e)

    owned_by_name_ci: dict[str, int] = {}
    names = sorted({e.name.strip() for e in name_only if e.name.strip()})
    if names:
        if on_status is not None:
            on_status(f"Deck validation: querying owned quantities for {len(names)} unique card name(s)")
        placeholders = ",".join(["?"] * len(names))
        q = f"""
            SELECT p.name AS name, COALESCE(SUM(op.quantity), 0) AS qty
            FROM owned_printings op
            JOIN printings p ON p.scryfall_id = op.scryfall_id
            WHERE (p.name COLLATE NOCASE) IN ({placeholders})
            GROUP BY p.name
        """
        for r in conn.execute(q, names).fetchall():
            key = str(r["name"]).casefold()
            owned_by_name_ci[key] = owned_by_name_ci.get(key, 0) + int(r["qty"] or 0)

    # Exact lines remain per-entry (usually very few).
    owned_exact: dict[tuple[str, str, str], int] = {}
    if on_status is not None and exact:
        on_status(f"Deck validation: querying {len(exact)} exact printing line(s)")
    for e in exact:
        key = (e.name.casefold(), (e.set_code or "").casefold(), e.collector_number or "")
        owned_exact[key] = _owned_quantity(conn, name=e.name, set_code=e.set_code, collector=e.collector_number)

    for e in entries:
        if e.set_code and e.collector_number:
            key = (e.name.casefold(), (e.set_code or "").casefold(), e.collector_number or "")
            owned = owned_exact.get(key, 0)
        else:
            owned = owned_by_name_ci.get(e.name.casefold(), 0)

        rows.append(
            ValidationRow(
                requested_qty=e.qty,
                owned_qty=owned,
                name=e.name,
                set_code=e.set_code,
                collector_number=e.collector_number,
            )
        )

    return rows


def _owned_quantity(conn, *, name: str, set_code: str | None, collector: str | None) -> int:
    name = name.strip()
    if not name:
        return 0

    if set_code and collector:
        row = conn.execute(
            """
            SELECT COALESCE(op.quantity, 0)
                        FROM owned_printings op
                        JOIN printings p ON p.scryfall_id = op.scryfall_id
            WHERE p.name = ? COLLATE NOCASE
              AND p.set_code = ? COLLATE NOCASE
              AND p.collector_number = ?
            LIMIT 1
            """,
            (name, set_code, collector),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    # Name-only: sum across all printings
    row = conn.execute(
        """
        SELECT COALESCE(SUM(op.quantity), 0)
        FROM owned_printings op
        JOIN printings p ON p.scryfall_id = op.scryfall_id
        WHERE p.name = ? COLLATE NOCASE
        """,
        (name,),
    ).fetchone()
    return int(row[0]) if row is not None else 0
