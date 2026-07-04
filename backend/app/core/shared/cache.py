"""File-based JSON caching utilities."""
import json
import os
from pathlib import Path


def default_cache_dir():
    return Path(__file__).resolve().parents[4] / ".eve_scanner_cache"


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
