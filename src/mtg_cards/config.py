from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mtg_cards.paths import data_dir


@dataclass(frozen=True)
class AppConfig:
    update_url: str = ""


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AppConfig()

    return AppConfig(update_url=str(payload.get("update_url") or "").strip())
