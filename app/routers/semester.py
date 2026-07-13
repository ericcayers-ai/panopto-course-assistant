"""routers/semester.py - semester planner: outlines, schedules, exports, announcements."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .. import context, paper_outlines, schedule_parser, settings_store, task_schedule
from .. import moodle_content
from ..context import _course_or_active
from ..paper_outlines import PaperOutlineError
from ..schemas import (MoodleAnnouncementsReq, PaperOutlineFetchReq, PaperSearchReq,
                       TaskScheduleBuildReq)

router = APIRouter()


@router.get("/api/semester/papers/search")
def api_paper_search(q: str, year: int = 2026) -> Dict[str, Any]:
    if not (q or "").strip():
        return {"results": []}
    results = paper_outlines.search_papers(q, year=year)
    return {"results": results}


@router.post("/api/semester/papers/search")
def api_paper_search_post(req: PaperSearchReq) -> Dict[str, Any]:
    results = paper_outlines.search_papers(req.query, year=req.year)
    return {"results": results}


@router.post("/api/semester/papers/fetch")
def api_paper_fetch(req: PaperOutlineFetchReq) -> Dict[str, Any]:
    code = req.paper_code or req.url
    if not code and not req.html:
        raise HTTPException(status_code=400, detail="paper_code, url, or html is required")
    try:
        outline = paper_outlines.fetch_outline(code, html=req.html)
    except PaperOutlineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    context.db.upsert_paper_outline(
        outline.get("paper_code", code),
        json.dumps(outline),
        title=outline.get("title", ""),
    )
    return outline


@router.get("/api/semester/papers/{paper_code}")
def api_paper_get(paper_code: str) -> Dict[str, Any]:
    row = context.db.get_paper_outline(paper_code)
    if row:
        return json.loads(row["outline_json"])
    try:
        outline = paper_outlines.fetch_outline(paper_code)
    except PaperOutlineError as e:
        raise HTTPException(status_code=404, detail=str(e))
    context.db.upsert_paper_outline(paper_code, json.dumps(outline), title=outline.get("title", ""))
    return outline


@router.post("/api/semester/schedule/import")
async def api_schedule_import(file: UploadFile = File(...),
                              course: Optional[int] = None) -> Dict[str, Any]:
    cid = _course_or_active(course)
    raw = await file.read()
    fname = (file.filename or "").lower()
    try:
        if fname.endswith(".csv"):
            text = raw.decode("utf-8-sig", errors="replace")
            tasks = schedule_parser.parse_notion_csv(text)
            parsed = {
                "name": Path(fname).stem,
                "files": [file.filename or "schedule.csv"],
                "task_count": len(tasks),
                "subjects": sorted({t["subject"] for t in tasks if t["subject"]}),
                "tasks": tasks,
            }
        else:
            parsed = schedule_parser.parse_notion_zip(raw)
    except schedule_parser.ScheduleParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sid = context.db.create_class_schedule(
        cid, parsed.get("name", file.filename or "Class schedule"),
        json.dumps(parsed), source_path=file.filename or "",
    )
    return {"id": sid, "course_id": cid, **parsed}


@router.get("/api/semester/schedules")
def api_class_schedules(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    rows = context.db.list_class_schedules(cid)
    out = []
    for r in rows:
        payload = json.loads(r["schedule_json"])
        out.append({
            "id": r["id"], "name": r["name"], "course_id": r["course_id"],
            "task_count": payload.get("task_count", 0),
            "subjects": payload.get("subjects", []),
            "created_at": r["created_at"],
        })
    return {"schedules": out}


@router.post("/api/semester/plan/build")
def api_plan_build(req: TaskScheduleBuildReq) -> Dict[str, Any]:
    if not req.paper_codes:
        raise HTTPException(status_code=400, detail="paper_codes is required")
    cid = _course_or_active(req.course_id)
    try:
        return task_schedule.build_schedule(
            context.db, cid,
            paper_codes=req.paper_codes,
            class_schedule_id=req.class_schedule_id,
            name=req.name,
        )
    except PaperOutlineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/semester/plans")
def api_plans_list(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    rows = context.db.list_task_schedules(cid)
    plans = []
    for r in rows:
        payload = json.loads(r["schedule_json"])
        plans.append({
            "id": r["id"], "name": r["name"], "course_id": r["course_id"],
            "paper_codes": r["paper_codes"],
            "task_count": payload.get("task_count", 0),
            "created_at": r["created_at"],
        })
    return {"plans": plans}


@router.get("/api/semester/plans/{plan_id}")
def api_plan_get(plan_id: int) -> Dict[str, Any]:
    row = context.db.get_task_schedule(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    payload = json.loads(row["schedule_json"])
    payload["id"] = row["id"]
    payload["name"] = row["name"]
    payload["timeline"] = task_schedule.semester_timeline(payload.get("tasks", []))
    return payload


@router.get("/api/semester/plans/{plan_id}/export/notion.csv")
def api_plan_export_notion(plan_id: int) -> Response:
    row = context.db.get_task_schedule(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    tasks = json.loads(row["schedule_json"]).get("tasks", [])
    csv_text = task_schedule.export_notion_csv(tasks)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="semester-plan-{plan_id}.csv"'},
    )


@router.get("/api/semester/plans/{plan_id}/export/obsidian.zip")
def api_plan_export_obsidian(plan_id: int) -> FileResponse:
    row = context.db.get_task_schedule(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    tasks = json.loads(row["schedule_json"]).get("tasks", [])
    out_dir = context.OUTPUT_DIR / "_semester"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"obsidian-plan-{plan_id}.zip"
    task_schedule.export_obsidian_zip(tasks, dest, title=row["name"])
    return FileResponse(dest, filename=dest.name, media_type="application/zip")


def _plan_outlines(payload: Dict[str, Any]) -> list:
    outlines = []
    for code in payload.get("outlines_used") or payload.get("paper_codes") or []:
        row = context.db.get_paper_outline(code)
        if row:
            outlines.append(json.loads(row["outline_json"]))
    return outlines


@router.get("/api/semester/plans/{plan_id}/export/calendar.ics")
def api_plan_export_calendar(plan_id: int) -> Response:
    row = context.db.get_task_schedule(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    payload = json.loads(row["schedule_json"])
    tasks = payload.get("tasks", [])
    ics = task_schedule.export_calendar_ics(
        tasks, outlines=_plan_outlines(payload), title=row["name"],
    )
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="semester-plan-{plan_id}.ics"'},
    )


@router.get("/api/semester/plans/{plan_id}/export/google-calendar.csv")
def api_plan_export_google_calendar(plan_id: int) -> Response:
    row = context.db.get_task_schedule(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    payload = json.loads(row["schedule_json"])
    tasks = payload.get("tasks", [])
    csv_text = task_schedule.export_google_calendar_csv(
        tasks, outlines=_plan_outlines(payload),
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="semester-plan-{plan_id}-google.csv"'},
    )


@router.post("/api/semester/moodle/announcements")
def api_moodle_announcements(req: MoodleAnnouncementsReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    try:
        fetched = moodle_content.fetch_announcements(req.url, req.cookies)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    n = context.db.replace_moodle_announcements(
        cid, fetched.get("moodle_course_id", ""),
        fetched.get("announcements", []),
    )
    return {"stored": n, "course_id": cid, **fetched}


@router.get("/api/semester/moodle/announcements")
def api_moodle_announcements_list(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(context.db)
    rows = context.db.list_moodle_announcements(cid)
    return {
        "announcements": [
            {"id": r["id"], "title": r["title"], "body": r["body"],
             "author": r["author"], "posted_at": r["posted_at"],
             "source_url": r["source_url"], "fetched_at": r["fetched_at"]}
            for r in rows
        ]
    }
