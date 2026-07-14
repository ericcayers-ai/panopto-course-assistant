"""
settings_store.py - persistent key/value preferences (§1).

Replaces "prefs live only in the browser's localStorage". Values are JSON-encoded
on the way in so numbers, booleans and nested dicts round-trip cleanly through the
TEXT ``settings.value`` column.

Reserved keys (not exposed through the public settings API):
    schema_version   - owned by database.migrate()
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


def ensure_active_course(db: Database, course_id: Optional[int] = None) -> int:
    """Return a valid ``courses.id``, creating a default row when none exist.

    Prefer ``course_id`` when it points at a real course, then the stored active
    course, then any non-archived course, otherwise create ``My course``.
    """
    candidates = []
    if course_id is not None:
        candidates.append(int(course_id))
    active = get_active_course(db)
    if active is not None:
        candidates.append(int(active))
    for cid in candidates:
        if db.get_course(cid) is not None:
            if get_active_course(db) != cid:
                set_active_course(db, cid)
            return cid
    rows = db.list_courses(include_archived=False) or db.list_courses(include_archived=True)
    if rows:
        cid = int(rows[0]["id"])
        set_active_course(db, cid)
        return cid
    cid = db.create_course("My course")
    set_active_course(db, cid)
    return cid
