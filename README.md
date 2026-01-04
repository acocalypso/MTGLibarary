# MTG Cards (Desktop)

Desktop app for managing a Magic: The Gathering card collection, importing CSV exports, and validating deck lists against owned cards.

## Scope

This app focuses on:

- Maintaining a local collection database (SQLite) backed by Scryfall card data.
- Importing collection quantities from CSV exports.
- Validating deck lists against what you own.

Out of scope (by design): online accounts/sync, price tracking, trading tools.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m mtg_cards
```

## Build a Windows executable

This project can be packaged into a standalone `.exe` using PyInstaller.

```powershell
pip install -e ".[dev]"
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

Output:

- `dist/MTGLibarary/MTGLibarary.exe`

Important: for the default build you must keep the entire `dist/MTGLibarary/` folder together (including the `_internal/` folder). If you move/copy only the `.exe`, it will fail to start.

To build a single-file executable instead:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -OneFile
```

## Folders

- `imports/` (and legacy `import/`): place CSV files to import.
- `data/`: local app data (SQLite DB, Scryfall bulk JSON cache, image cache). Ignored by git.

## CSV import

- Use **File → Import CSV…** (Ctrl+I) to add one or more CSV files into `imports/` and import them.
- Or drop CSV files manually into `imports/` and click **Import CSVs** in the Collection tab.

### Supported CSV exports

The importer supports generic CSVs and specific exports from:

- **ManaBox**: uses the `Scryfall ID` column when present (best match).
- **MysticTools**: supported as long as the export includes standard columns like `Name`, `Set code` (or `Set`), `Collector number`, and `Quantity`.

If `Scryfall ID` is missing, the importer falls back to matching by `Name` + `Set code` + `Collector number` (and then progressively looser matches).
