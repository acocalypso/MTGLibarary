from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import ijson
import requests

from mtg_cards.db import json_dumps, meta_set, transaction


ProgressCb = Callable[[int, int | None], None]

# If the bulk-data API call fails on first run, we still need a URL.
# This endpoint returns JSON including a `download_uri`, which `download_file` resolves.
DEFAULT_INITIAL_URL = "https://api.scryfall.com/bulk-data/default_cards"


@dataclass(frozen=True)
class BulkDataItem:
    type: str
    updated_at: str
    download_uri: str
    size: int | None = None
    content_type: str | None = None


def _http_get_json(url: str, *, timeout: int = 60) -> dict:
    r = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "mtg-cards/0.1 (+https://github.com/)",
            "Accept": "application/json",
        },
    )
    r.raise_for_status()
    return r.json()


def get_default_cards_bulk() -> BulkDataItem:
    data = _http_get_json("https://api.scryfall.com/bulk-data")
    items = data.get("data") or []
    for it in items:
        if isinstance(it, dict) and it.get("type") == "default_cards":
            return BulkDataItem(
                type=str(it.get("type") or "default_cards"),
                updated_at=str(it.get("updated_at") or ""),
                download_uri=str(it.get("download_uri") or ""),
                size=int(it.get("size")) if it.get("size") is not None else None,
                content_type=str(it.get("content_type")) if it.get("content_type") is not None else None,
            )

    raise RuntimeError("Scryfall bulk-data did not include default_cards")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_download_url(url: str) -> str:
    # Support the bulk-data item endpoint which returns JSON containing download_uri.
    if url.startswith("https://api.scryfall.com/") and ("bulk-data" in url):
        try:
            data = _http_get_json(url)
            if isinstance(data, dict) and data.get("download_uri"):
                return str(data["download_uri"])
        except Exception:
            return url
    return url


def download_file(url: str, dest: Path, *, on_progress: ProgressCb | None = None) -> None:
    url = _resolve_download_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    with requests.get(
        url,
        stream=True,
        timeout=60,
        headers={"User-Agent": "mtg-cards/0.1 (+https://github.com/)"},
    ) as r:
        r.raise_for_status()
        total: int | None = None
        cl = r.headers.get("Content-Length")
        if cl:
            try:
                total = int(cl)
            except ValueError:
                total = None

        downloaded = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)

    tmp.replace(dest)


def _is_token(card: dict) -> int:
    layout = str(card.get("layout") or "").lower()
    if layout.endswith("token"):
        return 1
    type_line = str(card.get("type_line") or "").lower()
    if type_line.startswith("token"):
        return 1
    return 0


def import_scryfall_all_cards_json(*, conn, json_path: Path, bulk_updated_at: str, on_status, on_progress=None) -> None:
    # Import is large; keep memory bounded by batching.
    batch: list[tuple] = []
    batch_size = 1000
    seen = 0

    # We'll report a scaled (0..1000) progress based on bytes read so we can keep
    # UI progress stable even when the JSON is >2GB.
    size: int | None = None
    try:
        size = int(json_path.stat().st_size)
    except Exception:
        size = None
    scale = 1000
    last_emit = 0.0

    def _emit_progress(f) -> None:
        nonlocal last_emit
        if on_progress is None or not size or size <= 0:
            return
        now = time.monotonic()
        if now - last_emit < 0.25:
            return
        last_emit = now
        try:
            pos = int(f.tell())
        except Exception:
            return
        current = int((pos / size) * scale)
        if current < 0:
            current = 0
        if current > scale:
            current = scale
        on_progress(current, scale)

    sql = """
        INSERT INTO printings(
            scryfall_id, oracle_id, name, set_code, collector_number,
            lang, released_at, mana_cost, type_line, oracle_text,
            colors, color_identity, rarity, image_uris, card_faces,
            is_token, is_digital
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    # Important: SQLite ignores PRAGMA foreign_keys changes while in a transaction.
    # We fully reload the `printings` table, so we temporarily disable FK enforcement
    # *outside* the transaction to avoid FK errors from `owned_printings`.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with transaction(conn):
            # Speed up bulk import by dropping indexes and recreating them after.
            conn.execute("DROP INDEX IF EXISTS idx_printings_name")
            conn.execute("DROP INDEX IF EXISTS idx_printings_set")
            conn.execute("DROP INDEX IF EXISTS idx_printings_type")

            conn.execute("DELETE FROM printings")

            on_status("Streaming Scryfall JSON…")
            with json_path.open("rb") as f:
                if on_progress is not None:
                    on_progress(0, scale)
                for card in ijson.items(f, "item"):
                    if not isinstance(card, dict):
                        continue

                    seen += 1
                    if seen % 5000 == 0:
                        on_status(f"Importing… {seen:,} cards")
                    _emit_progress(f)

                    scryfall_id = card.get("id")
                    if not scryfall_id:
                        continue

                    colors = card.get("colors")
                    color_identity = card.get("color_identity")
                    image_uris = card.get("image_uris")
                    card_faces = card.get("card_faces")

                    batch.append(
                        (
                            str(scryfall_id),
                            str(card.get("oracle_id") or "") or None,
                            str(card.get("name") or ""),
                            str(card.get("set") or "").lower(),
                            str(card.get("collector_number") or ""),
                            str(card.get("lang") or "") or None,
                            str(card.get("released_at") or "") or None,
                            str(card.get("mana_cost") or "") or None,
                            str(card.get("type_line") or "") or None,
                            str(card.get("oracle_text") or "") or None,
                            json_dumps(colors) if colors is not None else None,
                            json_dumps(color_identity) if color_identity is not None else None,
                            str(card.get("rarity") or "") or None,
                            json_dumps(image_uris) if image_uris is not None else None,
                            json_dumps(card_faces) if card_faces is not None else None,
                            _is_token(card),
                            1 if bool(card.get("digital")) else 0,
                        )
                    )

                    if len(batch) >= batch_size:
                        conn.executemany(sql, batch)
                        batch.clear()
                        _emit_progress(f)

            if batch:
                conn.executemany(sql, batch)
                batch.clear()

            # Recreate indexes after import.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_printings_name ON printings(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_printings_set ON printings(set_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_printings_type ON printings(type_line)")

            if bulk_updated_at:
                meta_set(conn, "scryfall.bulk.updated_at", bulk_updated_at)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    if on_progress is not None:
        on_progress(scale, scale)

    # Clean up any owned rows that reference vanished printings.
    conn.execute(
        "DELETE FROM owned_printings WHERE scryfall_id NOT IN (SELECT scryfall_id FROM printings)"
    )
    conn.commit()
