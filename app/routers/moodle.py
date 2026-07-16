"""routers/moodle.py - moodle endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

import os

from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request as _Request
from fastapi import UploadFile
from fastapi.responses import Response
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from urllib.parse import urlparse
from .. import core
from .. import courses
from .. import sources
from .. import sso_protocol
from ..imports import moodle_api
from ..imports import moodle_resources
from ..imports import moodle_web
from .. import context
from ..context import _MOODLE_PASSPORT, _audit
from ..jobs import manager
from .. import moodle_jobs
from ..schemas import DecodeLaunchTokenReq, FeedRequest, MoodleApiImportReq, MoodleConnectReq, MoodleFetchReq, MoodleRequest, MoodleUrlReq, PanoptoDiscoverReq, SsoCallbackReq

router = APIRouter()


@router.post("/api/moodle/panopto-feed")
def api_moodle_panopto_feed(req: FeedRequest) -> Dict[str, Any]:
    """Parse a Panopto podcast RSS feed into lecture recordings.

    Accepts either the audio (``type=mp3``) or video (``type=mp4``) podcast URL -
    the kind shown in Moodle's Panopto block - and fetches both variants so each
    recording carries a small audio ``url`` for transcription plus a ``video_url``
    for the SRT/recording export. Falls back to whichever feed is reachable.
    """
    variants = core.panopto_feed_variants(req.source)
    audio_items: List[core.LectureItem] = []
    video_items: List[core.LectureItem] = []
    errors: List[str] = []
    try:
        audio_items = core.parse_feed(variants["audio"], cookies=req.cookies)
    except Exception as e:
        errors.append(f"audio: {e}")
    try:
        video_items = core.parse_feed(variants["video"], cookies=req.cookies)
    except Exception as e:
        errors.append(f"video: {e}")
    if not audio_items and not video_items:
        raise HTTPException(
            status_code=400,
            detail=("Could not read the Panopto feed. The RSS URL usually needs "
                    "your Panopto/Moodle sign-in - open it in a browser first, or "
                    f"paste session cookies. ({'; '.join(errors)})"),
        )
    lectures = core.merge_panopto_variants(audio_items, video_items)
    return {"count": len(lectures), "lectures": lectures,
            "audio_feed": variants["audio"], "video_feed": variants["video"]}


@router.post("/api/moodle/parse")
def api_moodle_parse(req: MoodleRequest) -> Dict[str, Any]:
    """Parse a Moodle course HTML export into a structured outline."""
    try:
        parsed = sources.parse_moodle_course(Path(req.path).expanduser())
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse course page: {e}")
    if req.save_outline:
        parsed["saved_as"] = sources.save_outline(context.OUTPUT_DIR, parsed)
    return parsed


@router.post("/api/moodle/import-url")
def api_moodle_import_url(req: MoodleUrlReq) -> Dict[str, Any]:
    """Import a Moodle course from its live URL using the browser's session
    cookies (§7). Crawls linked section pages, recovers the outline + activities
    + Panopto feeds, and can create/activate a course from the page title."""
    try:
        parsed = moodle_web.import_course(req.url, req.cookies,
                                         follow_sections=req.follow_sections)
    except moodle_web.MoodleWebError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not import course: {e}")
    if req.save_outline:
        parsed["saved_as"] = sources.save_outline(context.OUTPUT_DIR, parsed)
    if req.create_course and (parsed.get("title") or parsed.get("code")):
        course = courses.create_course(context.db, name=parsed.get("title") or parsed["code"],
                                      code=parsed.get("code", ""))
        courses.set_active(context.db, course["id"])
        parsed["course"] = course
    return parsed


@router.post("/api/moodle/fetch-course")
def api_moodle_fetch_course(req: MoodleFetchReq) -> Dict[str, Any]:
    """Everything-from-the-link (§7): parse the course, download its resource
    files with your session cookies, convert them to Markdown (images attached
    unless ``keep_images`` is off), optionally export, and report the Panopto feeds
    so lectures can be transcribed. Requires internet + valid cookies."""
    try:
        parsed = moodle_web.import_course(req.url, req.cookies, follow_sections=True)
    except moodle_web.MoodleWebError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not read course: {e}")

    sources.save_outline(context.OUTPUT_DIR, parsed)

    # Only download + convert documents when the user ticked "Other docs".
    downloaded = {"downloaded": 0, "errors": []}
    converted = None
    if req.grab_docs:
        res_dir = context.OUTPUT_DIR / "_resources"
        try:
            downloaded = moodle_resources.download_resources(
                parsed.get("activities", []), res_dir, cookies=req.cookies)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Resource download failed: {e}")
        if req.convert and downloaded["downloaded"]:
            converted = core.convert_documents(res_dir, context.OUTPUT_DIR, target="ai",
                                              combined=True, keep_images=req.keep_images)
    _audit("moodle.fetch_course", target=parsed.get("code", ""),
           detail=f"resources={downloaded['downloaded']}", feature="moodle_import_url")

    exported = None
    if req.export == "notebooklm":
        exported = core.export_notebooklm(context.OUTPUT_DIR, combined=True,
                                         course=parsed.get("code", ""))
    elif req.export == "all":
        exported = core.export_all_sources(context.OUTPUT_DIR, combined=True,
                                          course=parsed.get("code", ""))

    # Only surface lecture feeds when the user ticked "Lectures".
    feeds = parsed.get("panopto_feeds", []) if req.grab_lectures else []
    return {"course": {"title": parsed.get("title"), "code": parsed.get("code")},
            "outline_sections": parsed.get("section_count"),
            "panopto_feeds": feeds,
            "resources": downloaded,
            "converted": converted,
            "exported": exported,
            "keep_images": req.keep_images}


@router.get("/api/moodle/launch-url")
def api_moodle_launch_url(url: str = "") -> Dict[str, Any]:
    """Return the Moodle mobile launch URL for browser-based SSO token acquisition.
    The user opens this URL, authenticates via their institution's SSO, and Moodle
    redirects to ``moodlemobile://token=<base64>`` - they copy that URL and we
    decode it via /api/moodle/decode-launch-token."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="Enter a Moodle site URL first.")
    try:
        launch = moodle_api.build_launch_url(url, passport=_MOODLE_PASSPORT)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"launch_url": launch}


@router.post("/api/moodle/decode-launch-token")
def api_moodle_decode_launch_token(req: DecodeLaunchTokenReq) -> Dict[str, Any]:
    """Decode the moodlemobile:// redirect URL that Moodle issues after a successful
    browser SSO login and return the web-service token (see moodle_api for format)."""
    try:
        token = moodle_api.decode_launch_token(req.raw, expected_passport=_MOODLE_PASSPORT)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": token}


@router.post("/api/moodle/sso-callback")
def api_moodle_sso_callback(req: SsoCallbackReq) -> Response:
    """Receives courseassistant://token=… from the Windows protocol handler and
    stores the decoded token for the next poll."""
    try:
        token = moodle_api.decode_launch_token(req.raw, expected_passport=_MOODLE_PASSPORT)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sso_protocol.store_token(token)
    return Response(status_code=204)


@router.get("/api/moodle/sso-poll")
def api_moodle_sso_poll(request: _Request) -> Dict[str, Any]:
    """Poll for a token delivered by the OS protocol handler.  Also re-registers
    the handler with the correct port on the first call (covers dev-server restarts)."""
    port = request.url.port or int(os.environ.get("CA_PORT", "8123"))
    sso_protocol.register(port)
    return {"token": sso_protocol.poll_token()}


@router.post("/api/moodle/connect")
def api_moodle_connect(req: MoodleConnectReq) -> Dict[str, Any]:
    """Connect to Moodle via the web-service API and list enrolled courses.

    Runs as a background job so progress and logs appear in the Jobs panel.
    """
    payload = req.model_dump()
    host_hint = ""
    try:
        host_hint = urlparse(moodle_api.normalize_base_url(req.url)).hostname or ""
    except moodle_api.MoodleApiError:
        pass
    title = f"Moodle connect{f' ({host_hint})' if host_hint else ''}"

    def work(progress):
        return moodle_jobs.run_moodle_connect(payload, progress)

    job = manager.submit(title, work, type="moodle_connect", payload=payload)
    return job.to_dict()


@router.post("/api/moodle/api-import")
def api_moodle_api_import(req: MoodleApiImportReq) -> Dict[str, Any]:
    """Import one Moodle course (browser scrape preferred, API fallback).

    Runs as a background job with staged logs for connect/import progress.
    """
    payload = req.model_dump()
    title = f"Moodle import (course {req.course_id})"

    def work(progress):
        return moodle_jobs.run_moodle_import(payload, progress)

    job = manager.submit(title, work, type="moodle_import", payload=payload)
    return job.to_dict()


@router.get("/api/moodle/capabilities")
def api_moodle_capabilities(mode: str = "api") -> Dict[str, Any]:
    """Capability matrix for API vs Browser Moodle import modes."""
    from .. import browser_scrape
    try:
        return browser_scrape.capability_matrix(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/panopto/discover")
def api_panopto_discover(req: PanoptoDiscoverReq) -> Dict[str, Any]:
    """Discover Panopto Podcast.ashx RSS feeds (Moodle HTML → cookies → Playwright)."""
    from .. import panopto_discover
    try:
        return panopto_discover.discover(
            moodle_html=req.moodle_html,
            moodle_url=req.moodle_url,
            panopto_url=req.panopto_url,
            cookies=req.cookies,
            use_playwright=req.use_playwright,
        )
    except panopto_discover.PanoptoDiscoverError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/api/moodle/quick-upload")
async def api_moodle_quick_upload(    files: List[UploadFile] = File(...),
    cookies: str = Form(""),
    convert: bool = Form(True),
    keep_images: bool = Form(True),
) -> Dict[str, Any]:
    """Saved-page importer for the quick flow. The user saves the rendered course
    page(s) - which the browser has fully populated, including the Panopto block's
    podcast feeds - and uploads them here.

    Multiple pages may be supplied and are merged: the main course page typically
    carries the Panopto lecture feeds, while section pages (e.g. a "Slides" folder)
    list the individual documents. Feeds are read directly from the markup and need
    no sign-in. If session cookies are provided, the linked documents are also
    downloaded and converted to Markdown."""
    feeds: List[str] = []
    activities: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    title = ""
    code = ""
    seen_feeds: set = set()
    seen_acts: set = set()

    for f in files:
        raw = (await f.read()).decode("utf-8", errors="replace")
        try:
            parsed = sources.parse_moodle_html(raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read {f.filename}: {e}")
        for feed in parsed.get("panopto_feeds", []):
            if feed not in seen_feeds:
                seen_feeds.add(feed); feeds.append(feed)
        for a in parsed.get("activities", []):
            key = a.get("url") or a.get("name")
            if key and key not in seen_acts:
                seen_acts.add(key); activities.append(a)
        sections += parsed.get("sections", [])
        # Prefer a real paper title over a section title like "Slides".
        t = parsed.get("title", "")
        if t and (not title or (parsed.get("panopto_feeds") and not code)):
            title = t
        if parsed.get("code"):
            code = code or parsed["code"]

    merged = {"title": title or (files[0].filename if files else "Course"),
              "code": code, "sections": sections, "section_count": len(sections),
              "activities": activities, "activity_count": len(activities),
              "panopto_feeds": feeds,
              "outline_markdown": sources._outline_markdown(title, code, sections, activities, [])}
    sources.save_outline(context.OUTPUT_DIR, merged)

    downloaded = {"downloaded": 0, "files": [], "errors": []}
    converted = None
    if cookies.strip() and activities:
        res_dir = context.OUTPUT_DIR / "_resources"
        try:
            downloaded = moodle_resources.download_resources(
                activities, res_dir, cookies=cookies)
            if convert and downloaded["downloaded"]:
                converted = core.convert_documents(res_dir, context.OUTPUT_DIR, target="ai",
                                                  combined=True, keep_images=keep_images)
        except Exception as e:
            downloaded["errors"].append({"name": "download", "error": str(e)})

    _audit("moodle.quick_upload", target=code,
           detail=f"feeds={len(feeds)} files={len(files)} docs={downloaded['downloaded']}",
           feature="moodle_import_url")
    return {"course": {"title": title, "code": code},
            "outline_sections": len(sections),
            "panopto_feeds": feeds,
            "resources": downloaded,
            "converted": converted,
            "from_file": ", ".join(f.filename or "page" for f in files)}
