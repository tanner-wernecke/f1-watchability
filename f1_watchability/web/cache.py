"""
File-based cache for scored race weekends.

Cache keys are derived from (year, meeting_key, config_hash) so that:
  - The same race with the same config is served instantly from disk
  - Changing weights.yaml automatically invalidates cached scores
  - No manual cache clearing is ever needed

Cache lives in a .cache/ directory at the project root.
Set F1_CACHE_DIR env var to override (e.g. for Railway volumes).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.environ.get("F1_CACHE_DIR", str(Path(__file__).parent.parent.parent / ".cache")))


def _ensure_cache_dir() -> None:
    _CACHE_DIR.mkdir(exist_ok=True)


def config_hash(config: dict) -> str:
    """Return a short hash of the config dict — changes when weights change."""
    serialised = json.dumps(config, sort_keys=True)
    return hashlib.sha256(serialised.encode()).hexdigest()[:12]


def cache_key(year: int, meeting_key: int, cfg_hash: str) -> str:
    return f"{year}_{meeting_key}_{cfg_hash}"


def get(year: int, meeting_key: int, cfg_hash: str) -> dict | None:
    """Return cached report dict or None if not cached."""
    path = _CACHE_DIR / f"{cache_key(year, meeting_key, cfg_hash)}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        logger.info(f"Cache hit: {path.name}")
        return data
    except Exception as e:
        logger.warning(f"Cache read error ({path.name}): {e}")
        return None


def set(year: int, meeting_key: int, cfg_hash: str, report_dict: dict) -> None:
    """Write report dict to cache."""
    _ensure_cache_dir()
    path = _CACHE_DIR / f"{cache_key(year, meeting_key, cfg_hash)}.json"
    try:
        with open(path, "w") as f:
            json.dump(report_dict, f)
        logger.info(f"Cache written: {path.name}")
    except Exception as e:
        logger.warning(f"Cache write error ({path.name}): {e}")


def clear_all() -> int:
    """Delete all cached results. Returns number of files deleted."""
    if not _CACHE_DIR.exists():
        return 0
    count = 0
    for f in _CACHE_DIR.glob("*.json"):
        f.unlink()
        count += 1
    return count


def cache_info() -> list[dict]:
    """Return metadata about all cached entries."""
    if not _CACHE_DIR.exists():
        return []
    results = []
    for f in sorted(_CACHE_DIR.glob("*.json")):
        parts = f.stem.split("_")
        results.append({
            "file":        f.name,
            "year":        parts[0] if len(parts) > 0 else "?",
            "meeting_key": parts[1] if len(parts) > 1 else "?",
            "config_hash": parts[2] if len(parts) > 2 else "?",
            "size_kb":     round(f.stat().st_size / 1024, 1),
        })
    return results
