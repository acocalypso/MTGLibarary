from __future__ import annotations

from pathlib import Path
import shutil
import sys
import os


def repo_root() -> Path:
    # Typical layout: <repo>/src/mtg_cards/paths.py
    # parents[3] => <repo>
    here = Path(__file__).resolve()
    candidate = here.parents[3]
    if (candidate / "pyproject.toml").exists() or (candidate / "README.md").exists():
        return candidate
    # Fallback (shouldn't happen in normal dev/editable installs)
    return here.parents[2]


def app_root() -> Path:
    """Base folder for app-owned writable files.

    In dev/editable installs, this is the repository root.
    In a frozen executable (PyInstaller), this is the folder containing the exe.
    """
    if bool(getattr(sys, "frozen", False)):
        # Onefile apps extract Python modules to a temporary directory, but the
        # executable itself lives where the user launched it from.
        return Path(sys.executable).resolve().parent
    return repo_root()


def user_data_root() -> Path:
    """Per-user writable root for app data.

    Used for frozen/installed builds where the install directory is typically
    under Program Files and not writable.
    """
    # Prefer LocalAppData (no roaming). Fall back to Roaming AppData, then home.
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "MTGLibarary"
    return Path.home() / ".mtg_cards"


def data_dir() -> Path:
    root = user_data_root() if bool(getattr(sys, "frozen", False)) else app_root()
    path = root / "data"
    path.mkdir(parents=True, exist_ok=True)

    # Migrate legacy location caused by an earlier bug (<repo>/src/data).
    legacy = root / "src" / "data"
    try:
        if legacy.exists() and legacy.is_dir():
            # Only migrate if the new location doesn't already have content.
            has_new_content = any(path.iterdir())
            if not has_new_content:
                for child in legacy.iterdir():
                    shutil.move(str(child), str(path / child.name))
    except Exception:
        # Best-effort migration only.
        pass

    return path


def image_cache_dir() -> Path:
    path = data_dir() / "image_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "mtg_cards.sqlite3"


def imports_dirs() -> list[Path]:
    if bool(getattr(sys, "frozen", False)):
        root = user_data_root()
        return [root / "imports", root / "import"]

    root = app_root()
    # Support both the spec folder (`imports/`) and a legacy folder (`import/`) already present.
    return [root / "imports", root / "import"]
