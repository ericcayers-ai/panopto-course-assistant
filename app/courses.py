"""
courses.py — multi-course CRUD service (§1).

Thin service layer over the database DAOs: validation, defaults, per-course
folders on disk, and the "active course" concept. Routes call these; all SQL
lives in ``database.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core, settings_store
from .database import Database
from .models import Course


def _course_dict(db: Database, row) -> Dict[str, Any]:
    d = Course.from_row(row).to_dict()
    d["counts"] = {
        "documents": db.count_documents(row["id"]),
        "transcripts": db.count_transcripts(row["id"]),
    }
    return d


def course_dir(db: Database, course_id: int) -> Path:
    """Deterministic per-course folder under the output root.

    Derived from code/name so it's stable across restarts. Not stored in the
    schema; renaming a course does not move its folder (kept simple for §1).
    """
    row = db.get_course(course_id)
    label = (row["code"] or row["name"]) if row else str(course_id)
    slug = core.safe_name(label, 60) or f"course_{course_id}"
    return db.root / slug


def create_course(db: Database, name: str, code: str = "", semester: str = "",
                  year: Optional[int] = None) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("course name is required")
    first = db.count_courses() == 0
    course_id = db.create_course(name, code.strip(), semester.strip(), year)
    # Best-effort per-course folder so imports have a home on disk.
    try:
        core.ensure_dir(course_dir(db, course_id))
    except Exception:
        pass
    if first:
        settings_store.set_active_course(db, course_id)
    return _course_dict(db, db.get_course(course_id))


def get_course(db: Database, course_id: int) -> Optional[Dict[str, Any]]:
    row = db.get_course(course_id)
    return _course_dict(db, row) if row else None


def list_courses(db: Database, include_archived: bool = True) -> List[Dict[str, Any]]:
    return [_course_dict(db, r) for r in db.list_courses(include_archived)]


def update_course(db: Database, course_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
    if "name" in fields and fields["name"] is not None:
        if not str(fields["name"]).strip():
            raise ValueError("course name cannot be empty")
        fields["name"] = str(fields["name"]).strip()
    if "archived" in fields and fields["archived"] is not None:
        fields["archived"] = 1 if fields["archived"] else 0
    db.update_course(course_id, **fields)
    return get_course(db, course_id)


def delete_course(db: Database, course_id: int) -> bool:
    ok = db.delete_course(course_id)
    if ok and settings_store.get_active_course(db) == course_id:
        # Fall back to any remaining course so the app always has an active one.
        remaining = db.list_courses(include_archived=False) or db.list_courses()
        settings_store.set_active_course(db, remaining[0]["id"] if remaining else None)
    return ok


def duplicate_course(db: Database, course_id: int) -> Optional[Dict[str, Any]]:
    """Create a new course from another's metadata (a fresh, empty shell).

    Content (documents/transcripts) stays with the original; only the course
    record is cloned, named ``"<name> (copy)"``.
    """
    src = db.get_course(course_id)
    if not src:
        return None
    return create_course(
        db,
        name=f"{src['name']} (copy)",
        code=src["code"] or "",
        semester=src["semester"] or "",
        year=src["year"],
    )


def set_active(db: Database, course_id: int) -> Optional[Dict[str, Any]]:
    if not db.get_course(course_id):
        return None
    settings_store.set_active_course(db, course_id)
    return get_course(db, course_id)


def get_active(db: Database) -> Optional[Dict[str, Any]]:
    active_id = settings_store.get_active_course(db)
    return get_course(db, active_id) if active_id is not None else None
