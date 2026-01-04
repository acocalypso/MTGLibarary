from __future__ import annotations

from pathlib import Path

from mtg_cards import __version__
from mtg_cards.config import load_config
from mtg_cards.csv_importer import ImportResult, import_csv_file
from mtg_cards.db import init_db, meta_get
from mtg_cards.paths import data_dir, imports_dirs
from mtg_cards.scryfall import (
    DEFAULT_INITIAL_URL,
    download_file,
    get_default_cards_bulk,
    import_scryfall_all_cards_json,
)
from mtg_cards.update import check_for_update


def scryfall_update_available(*, conn, on_status=None) -> bool:
    """Returns True if remote Scryfall bulk data is newer than the local DB.

    This is a "check-only" call: it does not download any bulk JSON.
    If the check cannot be performed (e.g., offline), it returns False.
    """
    try:
        if on_status:
            on_status("Checking Scryfall bulk data…")
        bulk = get_default_cards_bulk()
        local_updated_at = meta_get(conn, "scryfall.bulk.updated_at") or ""
        has_local = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] > 0
        if not has_local:
            return True
        if not bulk.updated_at:
            return False
        if not local_updated_at:
            return True
        return bulk.updated_at > local_updated_at
    except Exception:
        return False


def ensure_scryfall_up_to_date(
    *,
    conn,
    on_status,
    on_progress,
) -> bool:
    """Returns True if an import/update happened."""
    data = data_dir()
    local_updated_at = meta_get(conn, "scryfall.bulk.updated_at")

    # Determine remote bulk info (best effort).
    bulk = None
    try:
        on_status("Checking Scryfall bulk data…")
        bulk = get_default_cards_bulk()
    except Exception:
        bulk = None

    needs_initial = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0

    if needs_initial:
        on_status("Downloading Scryfall database (first run)…")
        url = DEFAULT_INITIAL_URL
        bulk_updated_at = bulk.updated_at if bulk else ""
    else:
        if bulk and local_updated_at and bulk.updated_at and bulk.updated_at <= local_updated_at:
            return False
        if not bulk:
            return False
        on_status("Downloading updated Scryfall database…")
        url = bulk.download_uri
        bulk_updated_at = bulk.updated_at

    dest = data / "scryfall_all_cards.json"

    def _progress(downloaded: int, total: int | None) -> None:
        # QProgressDialog/QProgressBar expect 32-bit signed ints in Qt.
        # Scryfall bulk JSON can exceed 2GB, so emitting raw byte counts
        # overflows on Windows/PySide (shiboken).
        if total is None or total <= 0:
            on_progress(0, 0)
            return

        # Emit a per-mille percentage for smoother progress.
        scale = 1000
        current = int((downloaded / total) * scale)
        if current < 0:
            current = 0
        if current > scale:
            current = scale
        on_progress(current, scale)

    download_file(url, dest, on_progress=_progress)

    on_status("Importing Scryfall database…")
    # Switch progress dialog to indeterminate for the import phase.
    on_progress(0, 0)
    import_scryfall_all_cards_json(
        conn=conn,
        json_path=dest,
        bulk_updated_at=bulk_updated_at,
        on_status=on_status,
        on_progress=on_progress,
    )

    return True


def import_all_csvs(
    *,
    conn,
    on_status,
    on_progress,
) -> list[ImportResult]:
    results: list[ImportResult] = []
    csv_files: list[Path] = []
    for folder in imports_dirs():
        if folder.exists():
            csv_files.extend(sorted(folder.glob("*.csv")))

    total = len(csv_files)
    done = 0
    for csv_path in csv_files:
        done += 1
        on_progress(done, total)
        on_status(f"Importing CSV {done}/{total}: {csv_path.name}")
        try:
            results.append(import_csv_file(conn, csv_path))
        except Exception:
            # Keep going; surface errors in UI logs.
            results.append(
                ImportResult(
                    file=csv_path,
                    skipped_already_imported=False,
                    rows_seen=0,
                    rows_imported=0,
                    rows_unmatched=0,
                    quantity_added=0,
                    distinct_printings_matched=0,
                )
            )

    return results


def check_app_update(*, on_status, on_progress) -> object:
    cfg = load_config()
    if not cfg.update_url:
        return None

    on_status("Checking for application updates…")
    return check_for_update(current_version=__version__, update_url=cfg.update_url)
