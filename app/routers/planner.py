"""routers/planner.py - planner endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from fastapi.responses import Response
from typing import Any
from typing import Dict
from typing import Optional
from .. import core
from .. import settings_store
from .. import study_planner
from .. import context
from ..context import _course_or_active
from ..schemas import AssessmentReq, AssessmentUpdate, GradeReq, QuizAttemptReq, StudySessionReq

router = APIRouter()


@router.get("/api/assessments")
def api_assessments_list(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return {"assessments": study_planner.list_assessments(context.db, cid)}


@router.post("/api/assessments")
def api_assessments_create(req: AssessmentReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    try:
        return study_planner.create_assessment(context.db, cid, req.name, req.due_date,
                                               req.weight, req.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/api/assessments/{assessment_id}")
def api_assessments_update(assessment_id: int, req: AssessmentUpdate) -> Dict[str, Any]:
    if not context.db.get_assessment(assessment_id):
        raise HTTPException(status_code=404, detail="Assessment not found")
    try:
        return study_planner.update_assessment(context.db, assessment_id,
                                               **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/assessments/{assessment_id}")
def api_assessments_delete(assessment_id: int) -> Dict[str, Any]:
    if not study_planner.delete_assessment(context.db, assessment_id):
        raise HTTPException(status_code=404, detail="Assessment not found")
    return {"deleted": assessment_id}


@router.get("/api/plan")
def api_plan(course: Optional[int] = None, horizon: int = 14,
            hours: float = 10.0) -> Dict[str, Any]:
    cid = _course_or_active(course)
    return study_planner.generate_plan(context.db, context.OUTPUT_DIR, cid, horizon_days=horizon,
                                      hours_per_week=hours)


@router.get("/api/calendar.ics")
def api_calendar(course: Optional[int] = None) -> Response:
    cid = _course_or_active(course)
    row = context.db.get_course(cid)
    name = (row["code"] or row["name"]) if row else "Course"
    ics = study_planner.build_ics(context.db, context.OUTPUT_DIR, cid, course_name=name)
    return Response(content=ics, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="course-{cid}.ics"'})


@router.post("/api/study-sessions")
def api_study_session(req: StudySessionReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    sid = context.db.log_study_session(cid, core.now_iso(), req.duration, req.activity_type)
    return {"id": sid, "course_id": cid, "duration": req.duration}


@router.get("/api/reviews")
def api_reviews(course: Optional[int] = None, due: str = "") -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    return {"reviews": study_planner.due_reviews(context.db, cid, due or None)}


@router.post("/api/reviews/{item_id}/grade")
def api_review_grade(item_id: int, req: GradeReq) -> Dict[str, Any]:
    out = study_planner.grade_review(context.db, item_id, req.quality)
    if out is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return out


@router.post("/api/quiz-attempts")
def api_quiz_attempt(req: QuizAttemptReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    aid = context.db.record_quiz_attempt(cid, req.scope, req.score, req.total, req.mode)
    return {"id": aid, "course_id": cid, "score": req.score, "total": req.total}


@router.get("/api/progress")
def api_progress(course: Optional[int] = None) -> Dict[str, Any]:
    cid = _course_or_active(course)
    return study_planner.progress(context.db, cid)
