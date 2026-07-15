"""routers/jobs.py - jobs endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from typing import Any
from typing import Dict
from .. import core
from .. import settings_store
from .. import transcribe
from ..jobs import manager
from .. import context
from ..context import JOB_FACTORIES, _make_transcribe_work
from ..schemas import OrganizeRequest, TranscribeRequest

router = APIRouter()


@router.post("/api/transcribe")
def api_transcribe(req: TranscribeRequest) -> Dict[str, Any]:
    status = transcribe.engine_status()
    if not status["any_engine"]:
        raise HTTPException(
            status_code=503,
            detail="No transcription engine installed. Install with: "
            "pip install -r requirements-transcribe.txt",
        )
    item = core.LectureItem(
        title=req.lecture.get("title", "lecture"),
        url=req.lecture.get("url", ""),
        size=int(req.lecture.get("size", 0) or 0),
        duration=int(req.lecture.get("duration", 0) or 0),
        pub_date=req.lecture.get("pub_date", ""),
        author=req.lecture.get("author", ""),
        guid=req.lecture.get("guid", ""),
    )
    if not item.url:
        raise HTTPException(status_code=400, detail="Lecture has no media URL")

    payload = req.model_dump()
    work = _make_transcribe_work(payload)
    job = manager.submit(item.title, work, type="transcribe", payload=payload,
                         course_id=settings_store.get_active_course(context.db))
    return job.to_dict()


@router.post("/api/organize")
def api_organize(req: OrganizeRequest) -> Dict[str, Any]:
    """Move existing transcripts into none/date/week/topic folders."""
    if req.by not in core.ORG_CHOICES:
        raise HTTPException(status_code=400, detail=f"organize must be one of {core.ORG_CHOICES}")
    moved = core.reorganize_outputs(context.OUTPUT_DIR, req.by)
    return {"moved": len(moved), "files": moved, "by": req.by}


@router.get("/api/jobs")
def api_jobs(status: str = "", type: str = "") -> Dict[str, Any]:
    jobs = manager.list()
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        jobs = [j for j in jobs if j.get("status") in wanted]
    if type:
        jobs = [j for j in jobs if j.get("type") == type]
    return {"jobs": jobs}


@router.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.get("/api/jobs/{job_id}/logs")
def api_job_logs(job_id: str) -> Dict[str, Any]:
    logs = manager.logs(job_id)
    if logs is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"id": job_id, "logs": logs}


@router.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str) -> Dict[str, Any]:
    if not manager.get(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    if not manager.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job already finished - nothing to cancel.")
    return {"id": job_id, "canceled": True}


@router.post("/api/jobs/{job_id}/retry")
def api_job_retry(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    factory = JOB_FACTORIES.get(job.type)
    if not factory:
        raise HTTPException(status_code=400,
                            detail=f"Don't know how to retry a '{job.type}' job.")
    if not job.payload:
        raise HTTPException(status_code=400,
                            detail="This job has no saved inputs to retry from.")
    retried = manager.retry(job_id, factory(job.payload))
    if retried is None:
        raise HTTPException(status_code=409, detail="Job is not in a retryable state.")
    return retried.to_dict()
