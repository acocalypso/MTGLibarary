from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from mtg_cards.paths import image_cache_dir


def cached_image_path(url: str) -> Path:
    h = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()  # noqa: S324
    return image_cache_dir() / f"{h}.img"


def fetch_image_to_cache(url: str) -> Path:
    dest = cached_image_path(url)
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 128):
                if chunk:
                    f.write(chunk)

    tmp.replace(dest)
    return dest
