"""Background workers for Moodle connect and import (API + browser scrape)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import browser_scrape, core, courses, moodle_calendar, secrets as secret_store, settings_store
from . import context
from .context import _audit, _moodle_token_name, _save_api_outline, _sso_provider_label
from .errors import AppError
from .imports import moodle_api, moodle_resources, moodle_sso, moodle_web


ProgressCb = Callable[[str, float], None]
MOODLE_COOKIES_PREFIX = "moodle_cookies:"


def _cookie_secret(host: str) -> str:
    return f"{MOODLE_COOKIES_PREFIX}{host}"


def _store_cookies(host: str, cookies: str) -> None:
    raw = (cookies or "").strip()
    if not raw:
        return
    secret_store.set_secret(_cookie_secret(host), raw, root=context.OUTPUT_DIR)


def _get_cookies(host: str, override: str = "") -> str:
    raw = (override or "").strip()
    if raw:
        return moodle_web.parse_cookies(raw)
    stored = secret_store.get_secret(_cookie_secret(host), root=context.OUTPUT_DIR)
    return moodle_web.parse_cookies(stored or "")


def _guarded_convert(res_dir: Path, *, keep_images: bool) -> Dict[str, Any]:
    """Run document conversion; never raise — return errors in the result dict."""
    try:
        return core.convert_documents(
            res_dir, context.OUTPUT_DIR, target="ai", combined=True, keep_images=keep_images,
        )
    except Exception as e:
        msg = str(e)
        if "markitdown" in msg.lower() or isinstance(e, ImportError):
            return {
                "count": 0, "files": [], "errors": [],
                "convert_error": (
                    "Document conversion failed — install markitdown: "
                    "pip install -r requirements-transcribe.txt"
                ),
            }
        return {"count": 0, "files": [], "errors": [], "convert_error": msg}


def _guarded_save_outline(model: Dict[str, Any]) -> Dict[str, Any]:
    try:
        rel = _save_api_outline(model)
        return {"outline": rel, "outline_error": ""}
    except Exception as e:
        return {"outline": "", "outline_error": str(e)}


def _web_model_from_parsed(parsed: Dict[str, Any], course_id: int) -> Dict[str, Any]:
    """Shape a moodle_web parse into an API-like import model."""
    activities = parsed.get("activities") or []
    sections = parsed.get("sections") or []
    docs = [
        {"filename": a.get("name") or "document", "category": a.get("kind") or "resource",
         "url": a.get("url") or ""}
        for a in activities
        if a.get("url") and a.get("kind") in ("resource", "document", "file", "")
    ]
    lectures = [
        {"name": a.get("name") or "Recording", "url": a.get("url") or ""}
        for a in activities
        if "panopto" in (a.get("url") or "").lower()
    ]
    code = parsed.get("code") or ""
    title = parsed.get("title") or code or "Course"
    outline_sections = []
    for s in sections:
        outline_sections.append({
            "name": s.get("topic") or s.get("name") or "",
            "week": s.get("week"),
            "modules": [{"name": a.get("name"), "kind": a.get("kind") or "activity"}
                        for a in activities if s.get("week") == a.get("week")][:20],
        })
    model = {
        "course": {
            "id": course_id,
            "fullname": title,
            "shortname": code,
            "code": code,
        },
        "sections": outline_sections or [{"name": "Course", "week": None, "modules": []}],
        "lectures": lectures,
        "documents": docs,
        "links": [{"name": a.get("name"), "url": a.get("url")}
                  for a in activities if a.get("kind") == "url"],
        "activities": activities,
        "panopto_feeds": parsed.get("panopto_feeds") or [],
        "counts": {
            "sections": len(sections),
            "lectures": len(lectures),
            "documents": len(docs),
            "links": len([a for a in activities if a.get("kind") == "url"]),
            "activities": len(activities),
            "panopto_feeds": len(parsed.get("panopto_feeds") or []),
        },
    }
    model["outline_markdown"] = moodle_api.render_outline(model)
    return model


def _merge_paper_codes(codes: List[str]) -> List[str]:
    if not codes:
        return []
    existing = settings_store.get(context.db, "semester.paper_codes", []) or []
    if not isinstance(existing, list):
        existing = []
    merged = list(dict.fromkeys([*existing, *codes]))
    settings_store.set(context.db, "semester.paper_codes", merged)
    return merged


def run_moodle_connect(payload: Dict[str, Any], progress: ProgressCb) -> Dict[str, Any]:
    progress("connecting", 0.05)
    url = payload.get("url") or ""
    try:
        base = moodle_api.normalize_base_url(url)
    except moodle_api.MoodleApiError as e:
        raise AppError(str(e), category="invalid_source", status_code=400) from e

    host = urlparse(base).hostname or ""
    token = (payload.get("token") or "").strip()
    username = payload.get("username") or ""
    password = payload.get("password") or ""
    cookies = payload.get("cookies") or ""
    if cookies.strip():
        _store_cookies(host, cookies)

    progress("authenticating", 0.15)
    try:
        if not token:
            if not (username and password):
                raise AppError(
                    "Enter your Moodle username and password, or paste a "
                    "web-service token from the Moodle mobile app.",
                    category="authentication", status_code=400,
                )
            try:
                token = moodle_api.fetch_token(base, username, password)
            except moodle_api.MoodleApiError:
                provider = moodle_sso.detect_sso(base)
                if provider:
                    raise moodle_api.MoodleApiError(
                        "SSO_REJECTED: This site signs in through "
                        f"{_sso_provider_label(provider)} - username/password can't be "
                        "used here. Use ‘Sign in via browser’ below to get your token."
                    ) from None
                raise
        progress("listing courses", 0.5)
        client = moodle_api.MoodleClient(base, token)
        info = client.site_info()
        courses_list = client.list_courses(info.get("userid"))
    except moodle_api.MoodleApiError as e:
        raise AppError(str(e), category="authentication", status_code=400) from e

    secret_store.set_secret(_moodle_token_name(host), token, root=context.OUTPUT_DIR)
    _audit("moodle.connect", target=host, feature="moodle_import_url")

    from . import suites
    paper_codes = suites.detect_paper_codes_from_courses(courses_list)
    if paper_codes:
        # Merge — never wipe codes already saved from other Moodle sites.
        paper_codes = _merge_paper_codes(paper_codes)

    progress("discovering calendar", 0.85)
    calendar_url = ""
    cookie_header = _get_cookies(host, cookies)
    if cookie_header:
        try:
            cal = browser_scrape.discover_calendar_url(base, cookies=cookie_header)
            calendar_url = cal.get("url") or ""
            if calendar_url:
                secret_store.set_secret(
                    moodle_calendar.MOODLE_CALENDAR_SECRET, calendar_url, root=context.OUTPUT_DIR,
                )
        except Exception:
            pass

    progress("done", 1.0)
    return {
        "host": host,
        "base_url": base,
        "sitename": info.get("sitename", ""),
        "fullname": info.get("fullname", ""),
        "courses": courses_list,
        "paper_codes": paper_codes,
        "calendar_url": moodle_calendar.mask_calendar_url(calendar_url),
        "calendar_discovered": bool(calendar_url),
    }


def run_moodle_import(payload: Dict[str, Any], progress: ProgressCb) -> Dict[str, Any]:
    progress("preparing", 0.02)
    url = payload.get("url") or ""
    course_id = int(payload.get("course_id") or 0)
    if not course_id:
        raise AppError("course_id is required", category="invalid_source", status_code=400)

    try:
        base = moodle_api.normalize_base_url(url)
    except moodle_api.MoodleApiError as e:
        raise AppError(str(e), category="invalid_source", status_code=400) from e

    host = urlparse(base).hostname or ""
    token = secret_store.get_secret(_moodle_token_name(host), root=context.OUTPUT_DIR)
    if not token:
        raise AppError(
            f"Not connected to {host or 'this Moodle site'} yet - connect first.",
            category="authentication", status_code=400,
        )

    use_browser = payload.get("use_browser", True)
    cookies_raw = payload.get("cookies") or ""
    if cookies_raw.strip():
        _store_cookies(host, cookies_raw)
    cookie_header = _get_cookies(host, cookies_raw)

    grab_docs = bool(payload.get("grab_docs", True))
    grab_lectures = bool(payload.get("grab_lectures", True))
    convert = bool(payload.get("convert", True))
    keep_images = bool(payload.get("keep_images", True))
    create_course = bool(payload.get("create_course", True))
    export_kind = payload.get("export") or ""

    model: Optional[Dict[str, Any]] = None
    import_mode = "api"
    scrape_notes: List[str] = []

    if use_browser and cookie_header:
        course_url = f"{base.rstrip('/')}/course/view.php?id={course_id}"
        progress("browser scrape", 0.1)
        try:
            parsed = moodle_web.import_course(course_url, cookie_header, follow_sections=True)
            if parsed.get("section_count", 0) > 0 or parsed.get("activities"):
                model = _web_model_from_parsed(parsed, course_id)
                import_mode = "browser"
                scrape_notes.append("Imported course HTML via browser session")
            else:
                scrape_notes.append("Browser scrape returned no sections — falling back to API")
        except moodle_web.MoodleWebError as e:
            scrape_notes.append(f"Browser scrape failed ({e}) — falling back to API")
        except Exception as e:
            scrape_notes.append(f"Browser scrape error ({e}) — falling back to API")

        if model and cookie_header:
            progress("scraping forums", 0.25)
            try:
                browser_scrape.scrape_moodle_announcements(course_url, cookies=cookie_header)
                scrape_notes.append("Scraped announcements")
            except Exception:
                scrape_notes.append("Announcements scrape skipped")
            try:
                browser_scrape.scrape_moodle_forums(course_url, cookies=cookie_header)
                scrape_notes.append("Scraped forums")
            except Exception:
                scrape_notes.append("Forums scrape skipped")

    if model is None:
        progress("api import", 0.35)
        client = moodle_api.MoodleClient(base, token)
        try:
            model = moodle_api.import_course(client, course_id)
        except moodle_api.MoodleApiError as e:
            raise AppError(str(e), category="invalid_source", status_code=400) from e
        import_mode = "api"

    progress("saving outline", 0.45)
    outline_info = _guarded_save_outline(model)
    outline_rel = outline_info["outline"]
    outline_error = outline_info.get("outline_error") or ""

    downloaded = {"downloaded": 0, "files": [], "errors": []}
    converted: Optional[Dict[str, Any]] = None
    convert_error = ""

    if grab_docs:
        progress("downloading docs", 0.55)
        res_dir = context.OUTPUT_DIR / "_resources"
        if import_mode == "browser" and cookie_header and model.get("activities"):
            try:
                downloaded = moodle_resources.download_resources(
                    model.get("activities") or [], res_dir, cookies=cookie_header,
                )
            except Exception as e:
                downloaded["errors"].append({"name": "download", "error": str(e)})
        elif model.get("documents"):
            client = moodle_api.MoodleClient(base, token)
            try:
                downloaded = moodle_api.download_documents(client, model["documents"], res_dir)
            except moodle_api.MoodleApiError as e:
                downloaded["errors"].append({"name": "download", "error": str(e)})

        if convert and downloaded.get("downloaded"):
            progress("converting", 0.7)
            converted = _guarded_convert(res_dir, keep_images=keep_images)
            convert_error = converted.pop("convert_error", "") or ""

    progress("discovering calendar", 0.82)
    calendar_url = ""
    if cookie_header:
        try:
            cal = browser_scrape.discover_calendar_url(base, cookies=cookie_header)
            calendar_url = cal.get("url") or ""
            if calendar_url:
                secret_store.set_secret(
                    moodle_calendar.MOODLE_CALENDAR_SECRET, calendar_url, root=context.OUTPUT_DIR,
                )
                scrape_notes.append("Calendar export URL discovered")
        except Exception:
            scrape_notes.append("Calendar discovery skipped")

    course_rec = None
    if create_course and (model["course"]["fullname"] or model["course"]["code"]):
        progress("creating course", 0.88)
        name = model["course"]["fullname"] or model["course"]["code"]
        code = (model["course"].get("code") or "").strip()
        course_rec = None
        if code:
            for existing in courses.list_courses(context.db, include_archived=False):
                if (existing.get("code") or "").strip().upper() == code.upper():
                    course_rec = courses.update_course(
                        context.db, existing["id"], name=name, code=code,
                    ) or existing
                    break
        if course_rec is None:
            course_rec = courses.create_course(context.db, name=name, code=code)
        courses.set_active(context.db, course_rec["id"])

    exported = None
    code = model["course"]["code"]
    if export_kind == "notebooklm":
        exported = core.export_notebooklm(context.OUTPUT_DIR, combined=True, course=code)
    elif export_kind == "all":
        exported = core.export_all_sources(context.OUTPUT_DIR, combined=True, course=code)

    from . import suites
    paper_codes = suites.detect_paper_codes_from_courses([model["course"]])
    if paper_codes:
        _merge_paper_codes(paper_codes)

    _audit(
        "moodle.api_import", target=code or host,
        detail=f"mode={import_mode} docs={downloaded['downloaded']} "
               f"lectures={model['counts']['lectures']}",
        feature="moodle_import_url",
    )

    progress("done", 1.0)
    result: Dict[str, Any] = {
        "import_mode": import_mode,
        "scrape_notes": scrape_notes,
        "course": {**model["course"], "local_course": course_rec},
        "counts": model["counts"],
        "panopto_feeds": model["panopto_feeds"] if grab_lectures else [],
        "lectures": model["lectures"] if grab_lectures else [],
        "documents": model["documents"],
        "links": model.get("links", []),
        "activities": model.get("activities", []),
        "resources": downloaded,
        "converted": converted,
        "exported": exported,
        "outline": outline_rel,
        "outline_error": outline_error,
        "convert_error": convert_error,
        "keep_images": keep_images,
        "paper_codes": paper_codes,
        "calendar_url": moodle_calendar.mask_calendar_url(calendar_url),
        "calendar_discovered": bool(calendar_url),
    }
    if outline_error or convert_error:
        result["warnings"] = [w for w in (outline_error, convert_error) if w]
    return result
