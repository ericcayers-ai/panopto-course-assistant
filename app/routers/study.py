"""routers/study.py - study endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional
from .. import citations
from .. import core
from .. import glossary
from .. import keywords
from .. import lectures
from .. import nextup
from .. import practice
from .. import settings_store
from .. import streak as streak_mod
from .. import studyguide
from .. import workload
from .. import context
from ..context import _active_course_name, _copy_export_files, _note_dict
from ..schemas import ExportNamedRequest, ItemTagReq, NoteReq, NoteUpdate, PracticeGradeReq

router = APIRouter()


@router.get("/api/streak")
def api_streak(course: Optional[int] = None,
               goal: int = streak_mod.DEFAULT_GOAL_MINUTES) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return streak_mod.compute(context.db, course_id=cid, goal_minutes=goal)


@router.get("/api/next-up")
def api_next_up(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return nextup.compute(context.db, context.OUTPUT_DIR, course_id=cid)


@router.get("/api/glossary")
def api_glossary(course: Optional[str] = None) -> Dict[str, Any]:
    name = course if course is not None else _active_course_name()
    return glossary.build_glossary(context.OUTPUT_DIR, course=name)


@router.post("/api/export/glossary")
def api_export_glossary(req: ExportNamedRequest) -> Dict[str, Any]:
    name = req.course or _active_course_name()
    result = glossary.write_glossary(context.OUTPUT_DIR, course=name)
    if result["count"] == 0:
        raise HTTPException(status_code=404,
                            detail="No terms found yet. Transcribe some lectures first.")
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        _copy_export_files([result["markdown"]], context.OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@router.get("/api/keywords")
def api_keywords(limit: int = 30) -> Dict[str, Any]:
    text = "\n".join(lec.get("text", "") for lec in lectures.iter_lectures(context.OUTPUT_DIR))
    return {"keywords": keywords.keywords(text, limit=limit),
            "phrases": keywords.key_phrases(text, limit=limit)}


@router.get("/api/workload")
def api_workload(read_wpm: int = workload.READ_WPM,
                 review_wpm: int = workload.REVIEW_WPM) -> Dict[str, Any]:
    return workload.estimate(context.OUTPUT_DIR, read_wpm=read_wpm, review_wpm=review_wpm)


@router.get("/api/study-guide")
def api_study_guide(course: Optional[str] = None) -> Dict[str, Any]:
    name = course if course is not None else _active_course_name()
    return studyguide.build_markdown(context.OUTPUT_DIR, course=name)


@router.post("/api/export/study-guide")
def api_export_study_guide(req: ExportNamedRequest) -> Dict[str, Any]:
    name = req.course or _active_course_name()
    result = studyguide.write_guide(context.OUTPUT_DIR, course=name)
    if result["lectures"] == 0:
        raise HTTPException(status_code=404,
                            detail="Nothing to build yet. Transcribe some lectures first.")
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        _copy_export_files([result["path"]], context.OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@router.get("/api/citations")
def api_citations(path: str) -> Dict[str, Any]:
    for g in core.list_transcripts(context.OUTPUT_DIR):
        if not core._is_transcript_group(g):
            continue
        if path != g.get("folder", "") + "/" + g["stem"] and \
                path not in g["formats"].values():
            continue
        meta = lectures.lecture_meta(context.OUTPUT_DIR, g)
        if not meta.get("course"):
            meta["course"] = _active_course_name()
        return {"path": path, "title": meta["title"],
                "citations": citations.cite_all(meta)}
    raise HTTPException(status_code=404, detail="Lecture not found")


@router.get("/api/practice-quiz")
def api_practice_quiz(course: Optional[int] = None, count: int = 10,
                      choices: int = 4, seed: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return practice.from_db(context.db, course_id=cid, count=count, choices=choices, seed=seed)


@router.post("/api/practice-quiz/grade")
def api_practice_grade(req: PracticeGradeReq) -> Dict[str, Any]:
    result = practice.grade(req.questions, req.answers)
    if req.record:
        cid = req.course_id if req.course_id is not None \
            else settings_store.get_active_course(context.db)
        if cid is not None:
            context.db.record_quiz_attempt(cid, "practice", result["score"],
                                   result["total"], "practice")
    return result


@router.get("/api/notes")
def api_notes_list(path: Optional[str] = None,
                   course: Optional[int] = None) -> Dict[str, Any]:
    rows = context.db.list_notes(path=path, course_id=course)
    return {"notes": [_note_dict(r) for r in rows]}


@router.post("/api/notes")
def api_notes_create(req: NoteReq) -> Dict[str, Any]:
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Note body is required.")
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    nid = context.db.add_note(req.path, req.body.strip(), course_id=cid,
                      timestamp_s=req.timestamp_s, bookmark=req.bookmark)
    row = context.db.query_one("SELECT * FROM notes WHERE id=?", (nid,))
    return _note_dict(row)


@router.patch("/api/notes/{note_id}")
def api_notes_update(note_id: int, req: NoteUpdate) -> Dict[str, Any]:
    if not context.db.update_note(note_id, **req.model_dump(exclude_none=True)):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"updated": note_id}


@router.delete("/api/notes/{note_id}")
def api_notes_delete(note_id: int) -> Dict[str, Any]:
    if not context.db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": note_id}


@router.get("/api/tags")
def api_tags_list(path: Optional[str] = None) -> Dict[str, Any]:
    if path is not None:
        return {"path": path, "tags": context.db.tags_for_path(path)}
    return {"tags": [{"name": r["name"], "count": r["n"], "color": r["color"]}
                     for r in context.db.list_tags()]}


@router.post("/api/tags")
def api_tags_add(req: ItemTagReq) -> Dict[str, Any]:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required.")
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    context.db.add_item_tag(req.path, name, course_id=cid)
    return {"path": req.path, "tags": context.db.tags_for_path(req.path)}


@router.delete("/api/tags")
def api_tags_remove(path: str, name: str) -> Dict[str, Any]:
    context.db.remove_item_tag(path, name)
    context.db.prune_unused_tags()
    return {"path": path, "tags": context.db.tags_for_path(path)}
