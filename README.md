# MTG Cards (Desktop)

Desktop app for managing a Magic: The Gathering card collection, importing CSV exports, and validating deck lists against owned cards.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m mtg_cards
```

## Folders

- `imports/` (and legacy `import/`): place CSV files to import.
- `data/`: local app data (SQLite DB, Scryfall bulk JSON cache, image cache). Ignored by git.

## CSV import

- Use **File → Import CSV…** (Ctrl+I) to add one or more CSV files into `imports/` and import them.
- Or drop CSV files manually into `imports/` and click **Import CSVs** in the Collection tab.
