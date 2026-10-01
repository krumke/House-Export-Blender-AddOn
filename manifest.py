"""JSON manifests. Lists instead of dictionaries so Unity's JsonUtility can read them."""

import json
import os
from datetime import datetime, timezone

MANIFEST_VERSION = 1


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
        f.write("\n")
    os.replace(tmp, path)


def module_entries(manifest):
    """name -> entry dict of an existing module manifest."""
    if not manifest:
        return {}
    return {e.get("name"): e for e in manifest.get("modules", []) if e.get("name")}
