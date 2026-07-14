"""routers/suites.py - study suite build + sync endpoints."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import context, settings_store, suites, task_schedule
from ..jobs import manager
from ..schemas import SuiteBuildReq, SuiteSettingsReq, SuiteSyncReq

router = APIRouter()


def _latest_plan(course_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    cid = course_id if course_id is not None else settings_store.get_active_course(context.db)
    if cid is None:
        return None
    rows = context.db.list_task_schedules(cid)
    return rows[0] if rows else None


def _plan_outlines(payload: Dict[str, Any]) -> list:
    outlines = []
    for code in payload.get("outlines_used") or payload.get("paper_codes") or []:
        row = context.db.get_paper_outline(code)
        if row:
            outlines.append(json.loads(row["outline_json"]))
    return outlines


def _announcements_for(course_id: int) -> List[Dict[str, Any]]:
    return [
        {"title": r["title"], "body": r["body"], "author": r["author"],
         "posted_at": r["posted_at"]}
        for r in context.db.list_moodle_announcements(course_id)
    ]


@router.get("/api/suites/formats")
def api_suite_formats() -> Dict[str, Any]:
    return {
        "formats": list(suites.SUITE_FORMATS),
        "folders": list(suites.SUITE_FOLDERS),
        "destinations": suites.get_destinations(context.db),
        "enabled": suites.get_enabled(context.db),
        "auto_sync": suites.get_auto_sync(context.db),
    }


@router.get("/api/suites/settings")
def api_suite_settings_get() -> Dict[str, Any]:
    return {
        "destinations": suites.get_destinations(context.db),
        "enabled": suites.get_enabled(context.db),
        "auto_sync": suites.get_auto_sync(context.db),
    }


@router.put("/api/suites/settings")
def api_suite_settings_put(req: SuiteSettingsReq) -> Dict[str, Any]:
    if req.destinations is not None:
        suites.set_destinations(context.db, req.destinations)
    if req.enabled is not None:
        suites.set_enabled(context.db, req.enabled)
    if req.auto_sync is not None:
        suites.set_auto_sync(context.db, req.auto_sync)
    return api_suite_settings_get()


@router.post("/api/suites/preview")
def api_suite_preview(req: SuiteBuildReq) -> Dict[str, Any]:
    row = None
    if req.plan_id:
        row = context.db.get_task_schedule(req.plan_id)
    else:
        row = _latest_plan()
    tasks: List[Dict[str, Any]] = []
    outlines: list = []
    title = req.title or "Semester plan"
    if row:
        payload = json.loads(row["schedule_json"])
        tasks = payload.get("tasks") or []
        outlines = _plan_outlines(payload)
        title = req.title or row["name"]
    return suites.preview_suite(
        format=req.format, title=title, tasks=tasks, outlines=outlines,
    )


@router.post("/api/suites/build")
def api_suite_build(req: SuiteBuildReq) -> Dict[str, Any]:
    row = None
    if req.plan_id:
        row = context.db.get_task_schedule(req.plan_id)
    else:
        row = _latest_plan()
    if not row:
        raise HTTPException(status_code=404, detail="No semester plan found. Run Sync or generate a plan first.")
    payload = json.loads(row["schedule_json"])
    tasks = payload.get("tasks") or []
    outlines = _plan_outlines(payload)
    title = req.title or row["name"]
    anns = _announcements_for(row["course_id"])
    dest = Path(req.dest_dir).expanduser() if req.dest_dir else (context.OUTPUT_DIR / "_suites" / req.format)
    try:
        built = suites.build_suite_tree(
            dest,
            format=req.format,
            title=title,
            tasks=tasks,
            outlines=outlines,
            announcements=anns or None,
            library_dir=context.OUTPUT_DIR,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result: Dict[str, Any] = {"ok": True, **built, "output": req.output}
    if req.output == "zip":
        zip_path = dest / f"{Path(built['root']).name}.zip"
        suites.zip_suite(Path(built["root"]), zip_path)
        result["zip"] = str(zip_path)
        result["download"] = f"/api/suites/download?path={zip_path.name}&format={req.format}"
    # Also mirror into configured destination when set
    destinations = suites.get_destinations(context.db)
    if destinations.get(req.format):
        mirrored = suites.mirror_tree(
            Path(built["root"]),
            Path(destinations[req.format]) / Path(built["root"]).name,
        )
        result["mirrored"] = mirrored
        result["destination"] = destinations[req.format]
    return result


@router.get("/api/suites/download")
def api_suite_download(path: str, format: str = "obsidian") -> FileResponse:
    safe = Path(path).name
    candidate = context.OUTPUT_DIR / "_suites" / format / safe
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Suite zip not found")
    return FileResponse(candidate, filename=safe, media_type="application/zip")


@router.post("/api/suites/sync")
def api_suite_sync(req: SuiteSyncReq) -> Dict[str, Any]:
    """Background job: refresh semester data (optional) then write/mirror suites."""
    cid = req.course_id if req.course_id is not None else settings_store.get_active_course(context.db)
    formats = req.formats or suites.get_enabled(context.db)
    payload_dump = req.model_dump()

    def work(progress):
        progress("Preparing suite sync...", 0.05)
        plan_row = None
        sync_report: Dict[str, Any] = {}
        paper_codes = list(req.paper_codes or [])

        if paper_codes:
            progress("Refreshing semester sources...", 0.2)
            from .. import secrets as secret_store
            from .. import moodle_calendar
            calendar_url = (req.calendar_url or "").strip()
            if not calendar_url:
                calendar_url = secret_store.get_secret(
                    moodle_calendar.MOODLE_CALENDAR_SECRET, root=context.OUTPUT_DIR,
                ) or ""
            sync_report = task_schedule.sync_semester_all(
                context.db, cid or 0,
                paper_codes=paper_codes,
                class_schedule_id=req.class_schedule_id,
                calendar_url=calendar_url or None,
                moodle_announcements_url=req.moodle_announcements_url,
                moodle_cookies=req.moodle_cookies,
                name=req.name,
            )
            plan_row = context.db.get_task_schedule(sync_report["plan_id"])
        else:
            plan_row = context.db.get_task_schedule(req.plan_id) if req.plan_id else _latest_plan(cid)

        if not plan_row:
            raise ValueError("No semester plan available. Provide paper_codes or generate a plan first.")

        plan_payload = json.loads(plan_row["schedule_json"])
        outlines = _plan_outlines(plan_payload)
        anns = _announcements_for(plan_row["course_id"])
        forums = None
        panopto_feeds: List[str] = []

        if req.discover_panopto:
            progress("Discovering Panopto feeds...", 0.55)
            try:
                from .. import panopto_discover
                discovered = panopto_discover.discover(
                    panopto_url=req.panopto_url,
                    cookies=req.moodle_cookies,
                    use_playwright=req.use_browser,
                )
                panopto_feeds = discovered.get("feeds") or []
                sync_report["panopto"] = discovered
            except Exception as e:
                sync_report["panopto"] = {"error": str(e), "feeds": []}

        if req.use_browser and req.moodle_announcements_url:
            progress("Scraping forums via browser...", 0.65)
            try:
                from .. import browser_scrape
                forums_result = browser_scrape.scrape_moodle_forums(
                    req.moodle_announcements_url, cookies=req.moodle_cookies,
                )
                forums = forums_result.get("forums")
                sync_report["forums"] = {"count": forums_result.get("count", 0)}
            except Exception as e:
                sync_report["forums"] = {"error": str(e)}

        progress("Writing suite trees...", 0.8)
        suite_result = suites.sync_suites_to_destinations(
            db=context.db,
            plan_payload=plan_payload,
            title=req.name or plan_row["name"],
            outlines=outlines,
            announcements=anns or None,
            forums=forums,
            library_dir=context.OUTPUT_DIR,
            formats=formats,
            staging_dir=context.OUTPUT_DIR / "_suites",
            push_live=req.push_live,
        )
        progress("done", 1.0)
        settings_store.set(context.db, "suite.last_sync", {
            "plan_id": plan_row["id"],
            "formats": formats,
            "new_files": suite_result.get("new_files", 0),
            "updated": suite_result.get("updated", 0),
        })
        return {
            "ok": True,
            "plan_id": plan_row["id"],
            "semester": sync_report,
            "panopto_feeds": panopto_feeds,
            "announcements": len(anns),
            **suite_result,
        }

    job = manager.submit(
        "Sync study suites", work,
        type="suite_sync", payload=payload_dump, course_id=cid,
    )
    return job.to_dict()
