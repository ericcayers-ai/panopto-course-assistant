"""
settings_store.py — persistent key/value preferences (§1).

Replaces "prefs live only in the browser's localStorage". Values are JSON-encoded
on the way in so numbers, booleans and nested dicts round-trip cleanly through the
TEXT ``settings.value`` column.

Reserved keys (not exposed through the public settings API):
    schema_version   — owned by database.migrate()
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .database import Database

# Internal keys that the settings API must never expose or let callers clobber.
_RESERVED = {"schema_version"}

ACTIVE_COURSE = "active_course"


def get(db: Database, key: str, default: Any = None) -> Any:
    raw = db.get_setting(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return raw  # tolerate a value written as a bare string

def set(db: Database, key: str, value: Any) -> None:
    db.set_setting(key, json.dumps(value))


def delete(db: Database, key: str) -> None:
    db.delete_setting(key)


def all(db: Database) -> Dict[str, Any]:
    """Every user-facing setting, JSON-decoded (reserved keys filtered out)."""
    out: Dict[str, Any] = {}
    for key, raw in db.all_settings().items():
        if key in _RESERVED:
            continue
        try:
            out[key] = json.loads(raw)
        except Exception:
            out[key] = raw
    return out


def update(db: Database, values: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a dict of preferences, ignoring reserved keys. Returns the new state."""
    for key, value in values.items():
        if key in _RESERVED:
            continue
        set(db, key, value)
    return all(db)


# -- active-course helpers (used by courses.py + routes) --------------------

def get_active_course(db: Database) -> Optional[int]:
    val = get(db, ACTIVE_COURSE)
    return int(val) if isinstance(val, int) or (isinstance(val, str) and val.isdigit()) else None


def set_active_course(db: Database, course_id: Optional[int]) -> None:
    if course_id is None:
        delete(db, ACTIVE_COURSE)
    else:
        set(db, ACTIVE_COURSE, int(course_id))
