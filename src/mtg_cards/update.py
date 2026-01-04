from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests
from packaging.version import Version


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    download_url: str | None
    notes: str | None


def check_for_update(*, current_version: str, update_url: str) -> UpdateInfo | None:
    """Checks a remote JSON endpoint for updates.

    The endpoint must return JSON like:
    {
      "version": "0.2.0",
      "url": "https://.../installer-or-zip",
      "notes": "..."
    }

    If update_url is empty, returns None.
    """
    if not update_url:
        return None

    r = requests.get(update_url, timeout=20)
    r.raise_for_status()
    payload: Any = r.json()

    latest = str(payload.get("version") or "").strip()
    if not latest:
        return None

    if Version(latest) <= Version(current_version):
        return None

    return UpdateInfo(
        latest_version=latest,
        download_url=(str(payload.get("url")).strip() if payload.get("url") else None),
        notes=(str(payload.get("notes")).strip() if payload.get("notes") else None),
    )
