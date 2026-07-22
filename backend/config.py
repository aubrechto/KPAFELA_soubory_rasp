"""JSON-based persistent configuration store for KAP{F}ELA."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
IMAGES_DIR = BASE_DIR / "static" / "images"

_lock = threading.Lock()


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.json"


def load(name: str) -> dict[str, Any]:
    """Load a JSON config file by logical name (settings, playlist, instruments)."""
    path = _path(name)
    with _lock:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)


def save(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Persist a JSON config file atomically and return the stored data."""
    path = _path(name)
    tmp = path.with_suffix(".json.tmp")
    with _lock:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(path)
    return data


def list_cover_images() -> list[str]:
    """Return the filenames of every cover image available on disk."""
    if not IMAGES_DIR.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    return sorted(p.name for p in IMAGES_DIR.iterdir() if p.suffix.lower() in exts)
