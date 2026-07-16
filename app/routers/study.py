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
from .. import essay_grader
from .. import glossary
from .. import keywords
from .. import lectures
from .. import nextup
from .. import notes_workspace
from .. import practice
from .. import settings_store
from .. import streak as streak_mod
from .. import study_modes
from .. import studyguide
from .. import workload
from .. import context
from ..context import _active_course_name, _copy_export_files, _note_dict
from ..schemas import (
    EssayGradeReq, ExportNamedRequest, FlashcardSetFromNoteReq, FocusCompleteReq,
    FocusStartReq, ItemTagReq, NoteFolderRename, NoteFolderReq, NoteImportReq,
    NoteReq, NoteUpdate, PracticeGradeReq,
)

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


@router.get("/api/study/daily-recall")
def api_daily_recall(course: Optional[int] = None, limit: int = 20) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return study_modes.daily_recall(context.db, cid, limit=limit)


@router.get("/api/study/slideshow")
def api_slideshow(course: Optional[int] = None, set_id: Optional[int] = None,
                  limit: int = 50) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    try:
        return study_modes.slideshow(context.db, cid, set_id=set_id, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/study/focus/start")
def api_focus_start(req: FocusStartReq) -> Dict[str, Any]:
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    if cid is None:
        raise HTTPException(status_code=400, detail="Select a course first.")
    return study_modes.start_focus(context.db, cid, minutes=req.minutes,
                                   activity_type=req.activity_type)


@router.post("/api/study/focus/complete")
def api_focus_complete(req: FocusCompleteReq) -> Dict[str, Any]:
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    if cid is None:
        raise HTTPException(status_code=400, detail="Select a course first.")
    return study_modes.complete_focus(context.db, cid, minutes=req.minutes,
                                      activity_type=req.activity_type,
                                      started_at=req.started_at)


@router.get("/api/study/tracker")
def api_study_tracker(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return study_modes.tracker_snapshot(context.db, cid)


@router.get("/api/notes/workspace")
def api_notes_workspace(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return notes_workspace.list_workspace(context.db, cid)


@router.get("/api/notes")
def api_notes_list(path: Optional[str] = None,
                   course: Optional[int] = None,
                   folder_id: Optional[int] = None,
                   session_type: Optional[str] = None) -> Dict[str, Any]:
    rows = context.db.list_notes(path=path, course_id=course,
                                 folder_id=folder_id, session_type=session_type)
    return {"notes": [_note_dict(r) for r in rows]}


@router.post("/api/notes")
def api_notes_create(req: NoteReq) -> Dict[str, Any]:
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Note body is required.")
    st = (req.session_type or "").strip().lower()
    if st and st not in notes_workspace.SESSION_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"session_type must be one of {notes_workspace.SESSION_TYPES}")
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    nid = context.db.add_note(req.path, req.body.strip(), course_id=cid,
                      timestamp_s=req.timestamp_s, bookmark=req.bookmark,
                      title=req.title.strip(), folder_id=req.folder_id,
                      session_type=st)
    row = context.db.query_one("SELECT * FROM notes WHERE id=?", (nid,))
    return _note_dict(row)


@router.patch("/api/notes/{note_id}")
def api_notes_update(note_id: int, req: NoteUpdate) -> Dict[str, Any]:
    data = req.model_dump(exclude_unset=True)
    if "session_type" in data:
        st = (data["session_type"] or "").strip().lower()
        if st and st not in notes_workspace.SESSION_TYPES:
            raise HTTPException(status_code=400,
                                detail=f"session_type must be one of {notes_workspace.SESSION_TYPES}")
        data["session_type"] = st
    if not context.db.update_note(note_id, **data):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"updated": note_id}


@router.delete("/api/notes/{note_id}")
def api_notes_delete(note_id: int) -> Dict[str, Any]:
    if not context.db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": note_id}


@router.post("/api/notes/import")
def api_notes_import(req: NoteImportReq) -> Dict[str, Any]:
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    try:
        return notes_workspace.import_file_as_note(
            context.db, req.path, course_id=cid, folder_id=req.folder_id,
            session_type=req.session_type, attach_path=req.attach_path,
            title=req.title)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/note-folders")
def api_note_folders(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return {"folders": [notes_workspace.folder_dict(r)
                        for r in context.db.list_note_folders(cid)]}


@router.post("/api/note-folders")
def api_note_folders_create(req: NoteFolderReq) -> Dict[str, Any]:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name is required.")
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    fid = context.db.create_note_folder(cid, name, parent_id=req.parent_id)
    return notes_workspace.folder_dict(context.db.get_note_folder(fid))


@router.patch("/api/note-folders/{folder_id}")
def api_note_folders_rename(folder_id: int, req: NoteFolderRename) -> Dict[str, Any]:
    if not context.db.rename_note_folder(folder_id, req.name):
        raise HTTPException(status_code=404, detail="Folder not found")
    return notes_workspace.folder_dict(context.db.get_note_folder(folder_id))


@router.delete("/api/note-folders/{folder_id}")
def api_note_folders_delete(folder_id: int) -> Dict[str, Any]:
    if not context.db.delete_note_folder(folder_id):
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"deleted": folder_id}


@router.get("/api/flashcard-sets")
def api_flashcard_sets(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return {"sets": [notes_workspace.set_dict(r)
                     for r in context.db.list_flashcard_sets(cid)]}


@router.post("/api/flashcard-sets/from-note")
def api_flashcard_sets_from_note(req: FlashcardSetFromNoteReq) -> Dict[str, Any]:
    try:
        return notes_workspace.create_set_from_note(
            context.db, req.note_id, name=req.name, max_cards=req.max_cards)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/flashcard-sets/{set_id}")
def api_flashcard_sets_delete(set_id: int) -> Dict[str, Any]:
    if not context.db.delete_flashcard_set(set_id):
        raise HTTPException(status_code=404, detail="Flashcard set not found")
    return {"deleted": set_id}


@router.post("/api/essay/grade")
def api_essay_grade(req: EssayGradeReq) -> Dict[str, Any]:
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(context.db)
    try:
        return essay_grader.grade_essay(
            req.essay, req.rubric, title=req.title, db=context.db,
            course_id=cid, save=req.save)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/essay/grades")
def api_essay_grades(course: Optional[int] = None, limit: int = 20) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    rows = context.db.list_essay_grades(cid, limit=limit)
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "course_id": r["course_id"], "title": r["title"],
            "score": r["score"], "originality": r["originality"],
            "created_at": r["created_at"],
        })
    return {"grades": out}


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
