"""routers/courses.py - courses endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from typing import Any
from typing import Dict
from .. import core
from .. import courses
from .. import exports as export_engine
from .. import settings_store
from .. import context
from ..context import _audit
from ..schemas import CourseCreate, CourseUpdate

router = APIRouter()


@router.get("/api/courses")
def api_courses_list(include_archived: bool = True) -> Dict[str, Any]:
    return {
        "courses": courses.list_courses(context.db, include_archived=include_archived),
        "active_course": settings_store.get_active_course(context.db),
    }


@router.post("/api/courses")
def api_courses_create(req: CourseCreate) -> Dict[str, Any]:
    try:
        return courses.create_course(context.db, name=req.name, code=req.code,
                                     semester=req.semester, year=req.year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/courses/{course_id}")
def api_courses_get(course_id: int) -> Dict[str, Any]:
    course = courses.get_course(context.db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@router.patch("/api/courses/{course_id}")
def api_courses_update(course_id: int, req: CourseUpdate) -> Dict[str, Any]:
    if not courses.get_course(context.db, course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        return courses.update_course(context.db, course_id, **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/courses/{course_id}")
def api_courses_delete(course_id: int) -> Dict[str, Any]:
    if not courses.delete_course(context.db, course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    return {"deleted": course_id, "active_course": settings_store.get_active_course(context.db)}


@router.post("/api/courses/{course_id}/duplicate")
def api_courses_duplicate(course_id: int) -> Dict[str, Any]:
    dup = courses.duplicate_course(context.db, course_id)
    if not dup:
        raise HTTPException(status_code=404, detail="Course not found")
    return dup


@router.post("/api/courses/{course_id}/activate")
def api_courses_activate(course_id: int) -> Dict[str, Any]:
    active = courses.set_active(context.db, course_id)
    if not active:
        raise HTTPException(status_code=404, detail="Course not found")
    return active


@router.post("/api/courses/{course_id}/export")
def api_courses_export(course_id: int) -> Dict[str, Any]:
    """Portable course archive (metadata + library + settings) - §9 Export Engine."""
    row = context.db.get_course(course_id)
    if not row:
        raise HTTPException(status_code=404, detail="Course not found")
    return export_engine.course_archive(context.OUTPUT_DIR, db=context.db, course_id=course_id,
                                       course=row["code"] or row["name"])


@router.post("/api/library/clear")
def api_library_clear() -> Dict[str, Any]:
    """Remove all course files (transcripts, documents, Notion pages and generated
    exports) from the library, keeping the database, secrets and backups intact.

    Destructive: the frontend confirms with the user before calling this."""
    removed = core.clear_library(context.OUTPUT_DIR)
    # Drop the matching index rows for the active course so counts stay consistent.
    cid = settings_store.get_active_course(context.db)
    if cid is not None:
        try:
            context.db.execute("DELETE FROM transcripts WHERE course_id=?", (cid,))
            context.db.execute("DELETE FROM documents WHERE course_id=?", (cid,))
        except Exception:
            pass
    _audit("library.clear", detail=f"{removed['files']} file(s) removed",
           feature="export")
    return {"ok": True, **removed}
