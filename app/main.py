"""
main.py - FastAPI backend for the Panopto Course Assistant.

Endpoints
---------
GET  /                     -> serves the frontend (static/index.html)
GET  /api/status           -> which optional engines/deps are installed
POST /api/feed             -> parse an RSS feed (URL or local path) -> lectures
POST /api/feed/upload      -> parse an uploaded RSS .xml file -> lectures
GET  /api/transcripts      -> list transcripts in the output directory
GET  /api/library          -> comprehensive, categorised listing of every file
GET  /api/transcript       -> read one transcript file (?path=)
GET  /api/search           -> full-text search across transcripts (?q=)
POST /api/export/notebooklm -> render transcripts into NotebookLM-friendly Markdown
POST /api/export/all        -> combine transcripts + documents + Notion into one AI export
POST /api/export/formats    -> generate subtitles / alternate formats from transcripts
POST /api/flashcards/generate -> Anki-importable flashcards from transcripts
POST /api/flashcards/categorize -> tag/categorise an existing flashcard deck
POST /api/export/notion-csv -> export a Notion-importable study-database CSV
POST /api/transcribe       -> queue a transcription job (needs whisper installed)
POST /api/organize         -> reorganize existing transcripts into folders
POST /api/moodle/parse     -> parse a whole Moodle course export into an outline
POST /api/notion/convert   -> convert a Notion export (.zip/.html/folder) into Markdown
POST /api/notion/upload    -> convert an uploaded Notion export (.zip/.html)
POST /api/docs/convert     -> convert documents (pdf/pptx/docx/…) to Markdown for AI
GET  /api/jobs             -> list jobs
GET  /api/jobs/{job_id}    -> one job's status
POST /api/pdf/convert      -> convert a folder of PDFs to Markdown
GET  /api/materials        -> browse files under a local folder
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, transcribe, sources, notion, flashcards, study, database, courses, settings_store, search, llm, ai, study_planner
from . import imageextract
from . import nativeui
from . import ollama_mgr
from . import cheatsheet as cheatsheet_mod
from . import secrets as secret_store
from . import exports as export_engine
from . import analytics
from . import backup as backup_mod
from . import (glossary, keywords, workload, nextup, citations, practice,
               studyguide, lectures)
from . import streak as streak_mod
from .integrations import notion as notion_sync, anki as anki_sync, state as sync_state
from .imports import moodle_web, folder as folder_import, preflight as import_preflight
from .imports import moodle_resources
from .imports import moodle_api
from .imports import moodle_sso
from . import sso_protocol
from .jobs import manager

BASE_DIR = Path(__file__).resolve().parent.parent
# Static assets live next to the app; CA_STATIC_DIR can override the location.
STATIC_DIR = Path(os.environ.get("CA_STATIC_DIR", BASE_DIR / "static"))
APP_VERSION = "3.0.0"
# Where transcripts are written/read. Override with PANOPTO_OUTPUT.
OUTPUT_DIR = Path(os.environ.get("PANOPTO_OUTPUT", BASE_DIR / "transcripts")).resolve()
core.ensure_dir(OUTPUT_DIR)

# --- Persistence (§1) ------------------------------------------------------
# Durable SQLite store lives alongside the library. Initialised at import so a
# reload (the test pattern) rebinds everything to the active output directory.
db = database.init(OUTPUT_DIR / "course_assistant.db")
manager.bind(db)          # jobs now survive restarts; crashed jobs -> 'interrupted'


def _backfill_library(database_, output_dir: Path) -> None:
    """One-time import of an existing ``transcripts/`` folder into the DB index.

    Runs only when the DB has no courses yet, so we never orphan files a user
    already produced before persistence existed (roadmap §Conventions). Idempotent
    via ``INSERT OR IGNORE`` on the file path.
    """
    if database_.count_courses() > 0:
        return
    groups = core.list_transcripts(output_dir)
    library = core.list_library(output_dir)
    documents = library["categories"]["documents"]
    if not groups and not documents:
        return
    course = courses.create_course(database_, name="My Course")
    cid = course["id"]
    for g in groups:
        fmts = g["formats"]
        primary = (fmts.get("txt") or fmts.get("md") or fmts.get("json")
                   or next(iter(fmts.values()), ""))
        if not primary:
            continue
        title = g["stem"]
        database_.insert_transcript(
            cid, title=title, path=primary,
            week=core.infer_week(title), topic=core.infer_topic(title),
        )
    for d in documents:
        database_.insert_document(
            cid, title=d["name"], path=d["path"], type="document",
            import_source="backfill",
        )


_backfill_library(db, OUTPUT_DIR)

# Default Swagger/ReDoc pull their JS+CSS from a CDN, so /docs is blank when the
# machine is offline. We disable them and serve a self-contained docs page below.
app = FastAPI(title="Course Assistant", version=APP_VERSION, docs_url=None, redoc_url=None)


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that tells browsers to always revalidate.

    Without this, assets are served with only an ETag/Last-Modified and browsers
    apply *heuristic caching* - happily serving a stale app.js/style.css after an
    update without checking. ``no-cache`` keeps the ETag fast-path (304 when
    unchanged) but forces a revalidation every load, so updates show immediately.
    """

    def file_response(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class FeedRequest(BaseModel):
    source: str
    cookies: str = ""


class TranscribeRequest(BaseModel):
    lecture: Dict[str, Any]          # a lecture dict as returned by /api/feed
    engine: str = "faster-whisper"
    model: str = "small"
    language: str = "en"
    device: str = "auto"
    organize: str = "auto"
    # Canonical set written for every transcription: clean text, Markdown, rich
    # JSON (everything else is derived from it), and a study summary. Subtitles
    # and other formats are generated on demand from the Export step.
    outputs: List[str] = ["txt", "md", "json", "summary"]
    interval: int = 30
    keep_media: bool = False
    audio_only: bool = False
    skip_existing: bool = True
    force: bool = False
    cookies: str = ""
    course: str = ""


class OrganizeRequest(BaseModel):
    by: str = "week"                 # auto | none | date | week | lecture | module | topic


class MoodleRequest(BaseModel):
    path: str                        # mirror folder or course/view_php.html
    save_outline: bool = False       # also write the outline as a source file


class NotionRequest(BaseModel):
    path: str                        # a Notion .html page or an export folder
    combined: bool = False           # also write a single notion_pack.md


class PdfRequest(BaseModel):
    input_path: str
    suffix: str = "_copy"
    include_subfolders: bool = True
    overwrite: bool = False


class DocsRequest(BaseModel):
    input_path: str
    exts: Optional[List[str]] = None     # default: all supported types
    include_subfolders: bool = True
    overwrite: bool = False
    target: str = "ai"                   # "ai" (_docs) | "copy" (sibling *_copy)
    combined: bool = False               # one documents_pack.md (ai target only)
    keep_images: bool = True             # extract & attach embedded images (default on)


class NotebookLMRequest(BaseModel):
    selection: Optional[List[str]] = None  # ["folder/stem", ...]; None = all
    combined: bool = False                 # also write a single course_pack.md
    course: str = ""                       # optional course name for headers
    output_dir: Optional[str] = None       # also copy files to this folder


class ExportAllRequest(BaseModel):
    combined: bool = True                  # write a single everything_pack.md
    course: str = ""                       # optional course name for headers
    output_dir: Optional[str] = None       # also copy files to this folder


class FormatsRequest(BaseModel):
    formats: List[str] = ["srt"]           # srt | vtt | txt | md | notebooklm | summary
    interval: int = 30


class FlashcardGenRequest(BaseModel):
    selection: Optional[List[str]] = None  # limit to these lecture stems; None = all
    course: str = ""
    deck: str = "flashcards"
    max_cards: int = 50                    # total max cards to generate
    output_dir: Optional[str] = None       # custom output folder


class FlashcardCatRequest(BaseModel):
    text: str = ""                         # pasted CSV/TSV deck (front, back[, tags])
    path: str = ""                         # …or a path to a .csv/.tsv/.txt deck
    course: str = ""
    deck: str = "categorized"


class StudyCsvRequest(BaseModel):
    course: str = ""
    filename: str = "study_database"
    output_dir: Optional[str] = None       # also copy CSV to this folder


class SrtExportRequest(BaseModel):
    output_dir: Optional[str] = None       # folder to copy SRT files alongside videos
    include_recordings: bool = True        # also place the lecture videos in that folder


class PickFolderRequest(BaseModel):
    title: str = "Choose a folder"


class PickSaveRequest(BaseModel):
    title: str = "Save as"
    default_name: str = ""
    ext: str = ""                          # e.g. ".pdf" / ".csv" for the save dialog


class OllamaPullRequest(BaseModel):
    model: str = ""


class OllamaUseRequest(BaseModel):
    model: str = ""


class OllamaInitRequest(BaseModel):
    model: str = ""


class CheatsheetRequest(BaseModel):
    course: str = ""
    max_pages: int = 1                     # A4 page budget for the cheat sheet
    save_path: Optional[str] = None        # exact PDF path chosen via Save As


# --- Multi-course / persistence (§1) ---------------------------------------


class CourseCreate(BaseModel):
    name: str
    code: str = ""
    semester: str = ""
    year: Optional[int] = None


class CourseUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    semester: Optional[str] = None
    year: Optional[int] = None
    archived: Optional[bool] = None


class SettingsUpdate(BaseModel):
    # Arbitrary preference bag (active_course, theme, export defaults, ai, sync…).
    # Stored JSON-encoded; reserved keys (schema_version) are ignored.
    values: Dict[str, Any]


class SavedViewCreate(BaseModel):
    name: str
    query: Dict[str, Any] = {}


# --- Optional AI / LLM (§4) ------------------------------------------------


class LLMSettings(BaseModel):
    values: Dict[str, Any]            # provider, model, temperature, max_tokens, retrieval_depth, host, api_key


class SummarizeReq(BaseModel):
    scope: str = "course"             # lecture | week | topic | course
    target: str = ""                  # path (lecture) | week number | topic


class FlashcardsAIReq(BaseModel):
    selection: Optional[List[str]] = None
    types: Optional[List[str]] = None
    course: str = ""
    max_cards: int = 20


class QuizReq(BaseModel):
    scope: str = "course"
    target: str = ""
    types: Optional[List[str]] = None
    difficulty: str = "medium"
    n: int = 8


class ChatReq(BaseModel):
    query: str
    history: Optional[List[Dict[str, str]]] = None


# --- Integrations / sync (§5) ----------------------------------------------


class NotionSyncReq(BaseModel):
    course: str = ""                   # course name for the Course property
    token: str = ""                    # overrides stored/env token
    database_id: str = ""              # target Notion DB (overrides stored)


class AnkiSyncReq(BaseModel):
    deck: str = "Course Assistant"
    course: str = ""
    selection: Optional[List[str]] = None   # limit flashcard source lectures
    url: str = ""                      # overrides stored AnkiConnect URL


class MappingReq(BaseModel):
    target: str                        # "notion"
    fields: Dict[str, str]             # local field -> remote property name


# --- Study planner (§6) ----------------------------------------------------


class AssessmentReq(BaseModel):
    name: str
    due_date: str = ""
    weight: Optional[float] = None
    status: str = "not_started"
    course_id: Optional[int] = None    # defaults to the active course


class AssessmentUpdate(BaseModel):
    name: Optional[str] = None
    due_date: Optional[str] = None
    weight: Optional[float] = None
    status: Optional[str] = None


class StudySessionReq(BaseModel):
    duration: int                      # minutes
    activity_type: str = ""
    course_id: Optional[int] = None


class QuizAttemptReq(BaseModel):
    scope: str = ""
    score: float = 0
    total: int = 0
    mode: str = ""
    course_id: Optional[int] = None


class GradeReq(BaseModel):
    quality: int                       # 0–5 recall score (SM-2)


# --- v3 study toolkit ------------------------------------------------------

class NoteReq(BaseModel):
    path: str
    body: str
    course_id: Optional[int] = None
    timestamp_s: Optional[float] = None
    bookmark: bool = False


class NoteUpdate(BaseModel):
    body: Optional[str] = None
    timestamp_s: Optional[float] = None
    bookmark: Optional[bool] = None


class ItemTagReq(BaseModel):
    path: str
    name: str
    course_id: Optional[int] = None


class ExportNamedRequest(BaseModel):
    course: str = ""
    output_dir: Optional[str] = None   # also copy the file to this folder


class PracticeGradeReq(BaseModel):
    questions: List[Dict[str, Any]]
    answers: List[Any]
    course_id: Optional[int] = None
    record: bool = True


# --- Import expansion (§7) -------------------------------------------------


class MoodleUrlReq(BaseModel):
    url: str                           # .../course/view.php?id=NNNNN
    cookies: str = ""                  # browser session cookies (header/txt)
    follow_sections: bool = True       # also crawl linked section.php pages
    save_outline: bool = True          # write the outline as an AI source
    create_course: bool = False        # create + activate a course from the title


class MoodleFetchReq(BaseModel):
    url: str                           # .../course/view.php?id=NNNNN
    cookies: str = ""                  # browser session cookies (header/txt)
    keep_images: bool = True           # attach images to converted docs (default on)
    convert: bool = True               # convert downloaded files to Markdown
    export: str = ""                   # ""|"notebooklm"|"all" - also export after
    grab_lectures: bool = True         # detect & return Panopto lecture feeds
    grab_docs: bool = True             # download + convert resource documents


# --- Moodle web-service import (token-based, replaces cookie/HTML scraping) ----


class MoodleConnectReq(BaseModel):
    url: str                           # site or course URL (host identifies the site)
    username: str = ""                 # for login/token.php token grant
    password: str = ""
    token: str = ""                    # …or paste a mobile web-service token (SSO sites)


class MoodleApiImportReq(BaseModel):
    url: str                           # site or course URL (host -> stored token)
    course_id: int                     # the course to import
    grab_lectures: bool = True         # surface lecture/recording feeds for transcription
    grab_docs: bool = True             # download + convert document files
    convert: bool = True               # convert downloaded files to Markdown
    keep_images: bool = True           # attach images to converted docs
    create_course: bool = False        # create + activate a local course from the title
    export: str = ""                   # ""|"notebooklm"|"all" - also export after


class FolderImportReq(BaseModel):
    path: str
    include_subfolders: bool = True
    course_id: Optional[int] = None    # defaults to the active course


class PreflightReq(BaseModel):
    path: str


# --- Security & privacy (§10) ----------------------------------------------


class SecretReq(BaseModel):
    value: str


# --- Export engine (§9) ----------------------------------------------------


class ExportReq(BaseModel):
    preset: str = ""                   # revision|ai|exam|notion|anki|archive
    target: str = ""                   # …or a single target directly
    scope: str = "course"              # lecture|week|topic|course|all
    scope_target: str = ""             # path/week/topic for narrowed scopes
    course: str = ""


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    status = transcribe.engine_status()
    status["output_dir"] = str(OUTPUT_DIR)
    status["output_choices"] = core.OUTPUT_CHOICES
    status["organize_choices"] = core.ORG_CHOICES
    status["doc_exts"] = core.DOC_EXTS
    status["db"] = {
        "schema_version": db.schema_version(),
        "courses": db.count_courses(),
        "active_course": settings_store.get_active_course(db),
    }
    status["ai"] = llm.detect()
    _cfg = llm.get_config(db, settings_store.get_active_course(db))
    status["ai"]["config"] = _safe_ai_config(_cfg)
    status["llm_ready"] = llm.is_enabled(_cfg)
    status["secrets"] = secret_store.backend_status()
    status["privacy"] = secret_store.transparency()
    status["transcribe_recommended"] = transcribe.recommend_settings()
    status["image_extraction"] = imageextract.capability()
    return status


@app.get("/api/transcribe/recommend")
def api_transcribe_recommend() -> Dict[str, Any]:
    """Best transcription settings for this machine (Simple-mode auto-transcribe)."""
    return transcribe.recommend_settings()


# ---------------------------------------------------------------------------
# Native OS dialogs (this app runs locally, so exports use a real file picker)
# ---------------------------------------------------------------------------


@app.post("/api/pick-folder")
def api_pick_folder(req: PickFolderRequest) -> Dict[str, Any]:
    """Open a native folder picker and return the chosen path (null if cancelled).

    ``available`` is false when no desktop dialog can be shown (e.g. a headless
    host), so the frontend can fall back to a typed path."""
    if not nativeui.available():
        return {"path": None, "available": False}
    path = nativeui.pick_directory(req.title or "Choose a folder", str(OUTPUT_DIR))
    return {"path": path, "available": True}


@app.post("/api/pick-save")
def api_pick_save(req: PickSaveRequest) -> Dict[str, Any]:
    """Open a native 'Save As' dialog and return the chosen file path (null if
    cancelled)."""
    if not nativeui.available():
        return {"path": None, "available": False}
    path = nativeui.pick_save_file(req.title or "Save as", req.default_name,
                                   str(OUTPUT_DIR), req.ext or "")
    return {"path": path, "available": True}


# ---------------------------------------------------------------------------
# Local Ollama: detect / start / pull models so AI features run inside the app
# ---------------------------------------------------------------------------


@app.get("/api/ollama/status")
def api_ollama_status() -> Dict[str, Any]:
    return ollama_mgr.status()


@app.post("/api/ollama/start")
def api_ollama_start() -> Dict[str, Any]:
    try:
        return ollama_mgr.start_server()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/ollama/pull")
def api_ollama_pull(req: OllamaPullRequest) -> Dict[str, Any]:
    """Download a model into the local server. Runs as a background job so the UI
    can show progress."""
    model = (req.model or ollama_mgr.DEFAULT_MODEL).strip()
    if not ollama_mgr.binary() and not ollama_mgr.is_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama is not installed. Install it from https://ollama.com/download.")

    def work(_progress):
        return ollama_mgr.pull_model(model, progress=lambda s, f: _progress(s, f))

    job = manager.submit(f"Ollama: pull {model}", work, type="ollama_pull",
                         payload={"model": model})
    return job.to_dict()


@app.post("/api/ollama/use")
def api_ollama_use(req: OllamaUseRequest) -> Dict[str, Any]:
    """Point the app's AI features at the local Ollama with the chosen model."""
    model = (req.model or ollama_mgr.DEFAULT_MODEL).strip()
    cid = settings_store.get_active_course(db)
    cfg = llm.set_config(db, cid, {"provider": "ollama", "model": model,
                                   "host": ollama_mgr.DEFAULT_HOST})
    return {"ok": True, "config": _safe_ai_config(cfg)}


@app.post("/api/ollama/install")
def api_ollama_install() -> Dict[str, Any]:
    """Run the official Ollama installer (Windows PowerShell). No-op if already
    installed. Returns ``{"ok": true}`` on success."""
    import sys
    if sys.platform != "win32":
        raise HTTPException(status_code=400,
                            detail="Automated install is only supported on Windows. "
                                   "Install Ollama from https://ollama.com/download.")
    try:
        return ollama_mgr.install_windows()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/ollama/initialize")
def api_ollama_initialize(req: OllamaInitRequest) -> Dict[str, Any]:
    """One-click: start server, pull model if not installed, activate for AI features.

    If Ollama itself is not installed, returns ``{"installed": false}`` so the
    frontend can call ``/api/ollama/install`` first.
    """
    model = (req.model or ollama_mgr.DEFAULT_MODEL).strip()
    s = ollama_mgr.initialize_model(model)
    if not s.get("installed", True):
        return s   # frontend handles the not-installed case
    # Wire up the LLM config so AI features (flashcards, cheat sheet…) activate.
    cid = settings_store.get_active_course(db)
    llm.set_config(db, cid, {"provider": "ollama", "model": model,
                              "host": ollama_mgr.DEFAULT_HOST})
    return s


def _safe_ai_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Never echo a stored API key back to the client - report only its presence."""
    out = {k: v for k, v in cfg.items() if k != "api_key"}
    out["has_api_key"] = bool(cfg.get("api_key"))
    return out


def _copy_export_files(rel_paths: List[str], src_root: Path, dest_dir: Path) -> None:
    """Copy exported files (given as rel paths from src_root) into dest_dir,
    flattening the hierarchy so all files land directly in dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        if not rel:
            continue
        src = src_root / rel
        if src.exists():
            shutil.copy2(src, dest_dir / src.name)


def _find_media(folder: Path, stem: str) -> Optional[Path]:
    """Return the lecture recording for ``stem`` inside ``folder`` if one exists."""
    if not folder.is_dir():
        return None
    for ext in folder_import.MEDIA_EXTS:
        cand = folder / f"{stem}{ext}"
        if cand.exists():
            return cand
    return None


def _export_recordings(dest_dir: Path, cookies: str = "") -> Dict[str, Any]:
    """Ensure each transcribed lecture's recording sits next to its exported SRT.

    For every transcript we copy the recording from the library if it was kept
    there, otherwise we try to (re)download it from the source URL stored in the
    lecture's JSON. Subtitle players auto-load a ``.srt`` when the video shares
    its folder and stem, so this makes the SRT export self-contained.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    downloaded: List[str] = []
    missing: List[str] = []
    for g in core.list_transcripts(OUTPUT_DIR):
        fmts = g.get("formats", {})
        if "json" not in fmts:
            continue
        stem = g["stem"]
        if _find_media(dest_dir, stem):
            copied.append(stem)            # already present in the destination
            continue
        json_path = OUTPUT_DIR / fmts["json"]
        lib_media = _find_media(json_path.parent, stem)
        if lib_media:
            try:
                shutil.copy2(lib_media, dest_dir / lib_media.name)
                copied.append(stem)
                continue
            except OSError:
                pass
        # Not kept in the library - re-download from the stored source URL.
        # Prefer the mp4 video URL (we transcribe from audio, so `url` may be mp3).
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            url = (data.get("video_url") or data.get("url") or "").strip()
        except (json.JSONDecodeError, OSError):
            url = ""
        if url:
            try:
                transcribe.download_media(url, dest_dir / f"{stem}.mp4", cookies=cookies)
                downloaded.append(stem)
                continue
            except Exception:
                pass
        missing.append(stem)
    return {"copied": copied, "downloaded": downloaded, "missing": missing,
            "have": len(copied) + len(downloaded)}


# ---------------------------------------------------------------------------
# Courses (§1 - multi-course foundation)
# ---------------------------------------------------------------------------


@app.get("/api/courses")
def api_courses_list(include_archived: bool = True) -> Dict[str, Any]:
    return {
        "courses": courses.list_courses(db, include_archived=include_archived),
        "active_course": settings_store.get_active_course(db),
    }


@app.post("/api/courses")
def api_courses_create(req: CourseCreate) -> Dict[str, Any]:
    try:
        return courses.create_course(db, name=req.name, code=req.code,
                                     semester=req.semester, year=req.year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/courses/{course_id}")
def api_courses_get(course_id: int) -> Dict[str, Any]:
    course = courses.get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@app.patch("/api/courses/{course_id}")
def api_courses_update(course_id: int, req: CourseUpdate) -> Dict[str, Any]:
    if not courses.get_course(db, course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        return courses.update_course(db, course_id, **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/courses/{course_id}")
def api_courses_delete(course_id: int) -> Dict[str, Any]:
    if not courses.delete_course(db, course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    return {"deleted": course_id, "active_course": settings_store.get_active_course(db)}


@app.post("/api/courses/{course_id}/duplicate")
def api_courses_duplicate(course_id: int) -> Dict[str, Any]:
    dup = courses.duplicate_course(db, course_id)
    if not dup:
        raise HTTPException(status_code=404, detail="Course not found")
    return dup


@app.post("/api/courses/{course_id}/activate")
def api_courses_activate(course_id: int) -> Dict[str, Any]:
    active = courses.set_active(db, course_id)
    if not active:
        raise HTTPException(status_code=404, detail="Course not found")
    return active


@app.post("/api/courses/{course_id}/export")
def api_courses_export(course_id: int) -> Dict[str, Any]:
    """Portable course archive (metadata + library + settings) - §9 Export Engine."""
    row = db.get_course(course_id)
    if not row:
        raise HTTPException(status_code=404, detail="Course not found")
    return export_engine.course_archive(OUTPUT_DIR, db=db, course_id=course_id,
                                       course=row["code"] or row["name"])


@app.post("/api/library/clear")
def api_library_clear() -> Dict[str, Any]:
    """Remove all course files (transcripts, documents, Notion pages and generated
    exports) from the library, keeping the database, secrets and backups intact.

    Destructive: the frontend confirms with the user before calling this."""
    removed = core.clear_library(OUTPUT_DIR)
    # Drop the matching index rows for the active course so counts stay consistent.
    cid = settings_store.get_active_course(db)
    if cid is not None:
        try:
            db.execute("DELETE FROM transcripts WHERE course_id=?", (cid,))
            db.execute("DELETE FROM documents WHERE course_id=?", (cid,))
        except Exception:
            pass
    _audit("library.clear", detail=f"{removed['files']} file(s) removed",
           feature="export")
    return {"ok": True, **removed}


# ---------------------------------------------------------------------------
# Settings (§1 - persistent preferences)
# ---------------------------------------------------------------------------


@app.get("/api/settings")
def api_settings_get() -> Dict[str, Any]:
    return settings_store.all(db)


@app.put("/api/settings")
def api_settings_update(req: SettingsUpdate) -> Dict[str, Any]:
    return settings_store.update(db, req.values)


# ---------------------------------------------------------------------------
# Optional AI / LLM (§4) - every endpoint degrades to an extractive fallback
# ---------------------------------------------------------------------------


@app.get("/api/llm/providers")
def api_llm_providers() -> Dict[str, Any]:
    return llm.detect()


@app.get("/api/llm/settings")
def api_llm_settings_get() -> Dict[str, Any]:
    return _safe_ai_config(llm.get_config(db, settings_store.get_active_course(db)))


@app.patch("/api/llm/settings")
def api_llm_settings_update(req: LLMSettings) -> Dict[str, Any]:
    cfg = llm.set_config(db, settings_store.get_active_course(db), req.values)
    return _safe_ai_config(cfg)


@app.post("/api/llm/summarize")
def api_llm_summarize(req: SummarizeReq) -> Dict[str, Any]:
    return ai.summarize(OUTPUT_DIR, req.scope, req.target, db=db,
                       course_id=settings_store.get_active_course(db))


@app.post("/api/llm/flashcards")
def api_llm_flashcards(req: FlashcardsAIReq) -> Dict[str, Any]:
    return ai.generate_flashcards(OUTPUT_DIR, selection=req.selection, types=req.types,
                                 course=req.course, max_cards=req.max_cards, db=db,
                                 course_id=settings_store.get_active_course(db))


@app.post("/api/llm/quiz")
def api_llm_quiz(req: QuizReq) -> Dict[str, Any]:
    return ai.generate_quiz(OUTPUT_DIR, req.scope, req.target, types=req.types,
                           difficulty=req.difficulty, n=req.n, db=db,
                           course_id=settings_store.get_active_course(db))


@app.post("/api/llm/chat")
def api_llm_chat(req: ChatReq) -> Dict[str, Any]:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Ask a question first.")
    cid = settings_store.get_active_course(db)
    out = ai.chat(OUTPUT_DIR, req.query, history=req.history, db=db, course_id=cid)
    if out.get("generated") == "ai":      # only a cloud/local provider call leaves a trace
        cfg = llm.get_config(db, cid)
        if cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.chat", target=cfg.get("provider", ""),
                   detail="RAG chat", feature="ai_cloud")
    return out


# ---------------------------------------------------------------------------
# Integrations - live Notion / Anki sync (§5). Every write is incremental,
# duplicate-aware, and offered as a dry-run first. Tokens are stored via §10.
# ---------------------------------------------------------------------------


@app.get("/api/sync/status")
def api_sync_status() -> Dict[str, Any]:
    return sync_state.public_status(db)


@app.put("/api/sync/mapping")
def api_sync_mapping(req: MappingReq) -> Dict[str, Any]:
    if req.target != "notion":
        raise HTTPException(status_code=400, detail="Only the Notion mapping is editable.")
    cfg = sync_state.set_target(db, "notion", {"field_map": req.fields})
    return {"target": "notion", "field_map": cfg["notion"]["field_map"]}


def _notion_args(req: NotionSyncReq) -> Dict[str, Any]:
    cfg = sync_state.get(db)["notion"]
    token = sync_state.notion_token(db, req.token)
    database_id = req.database_id or cfg.get("database_id", "")
    if not token:
        raise HTTPException(status_code=400, detail="No Notion token configured (set it in Sync settings or NOTION_TOKEN).")
    if not database_id:
        raise HTTPException(status_code=400, detail="No Notion database_id configured.")
    return {"token": token, "database_id": database_id,
            "course": req.course, "field_map": cfg.get("field_map")}


@app.post("/api/sync/notion/dryrun")
def api_sync_notion_dryrun(req: NotionSyncReq) -> Dict[str, Any]:
    try:
        return notion_sync.sync_course(OUTPUT_DIR, dry_run=True, **_notion_args(req))
    except notion_sync.NotionError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/sync/notion")
def api_sync_notion(req: NotionSyncReq) -> Dict[str, Any]:
    try:
        result = notion_sync.sync_course(OUTPUT_DIR, dry_run=False, **_notion_args(req))
    except notion_sync.NotionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    sync_state.set_target(db, "notion", {"last_sync": core.now_iso(),
                                        "database_id": _notion_args(req)["database_id"]})
    _audit("sync.notion", target="notion",
           detail=f"created={result.get('created',0)} updated={result.get('updated',0)}",
           feature="sync_notion")
    return result


def _anki_cards(req: AnkiSyncReq) -> List[Dict[str, Any]]:
    out = ai.generate_flashcards(OUTPUT_DIR, selection=req.selection, course=req.course,
                                db=db, course_id=settings_store.get_active_course(db))
    return out.get("cards", [])


@app.post("/api/sync/anki/dryrun")
def api_sync_anki_dryrun(req: AnkiSyncReq) -> Dict[str, Any]:
    url = req.url or sync_state.get(db)["anki"].get("url", "")
    return anki_sync.sync_flashcards(_anki_cards(req), req.deck, course=req.course,
                                    dry_run=True, url=url)


@app.post("/api/sync/anki")
def api_sync_anki(req: AnkiSyncReq) -> Dict[str, Any]:
    url = req.url or sync_state.get(db)["anki"].get("url", "")
    try:
        result = anki_sync.sync_flashcards(_anki_cards(req), req.deck, course=req.course,
                                          dry_run=False, url=url)
    except anki_sync.AnkiError as e:
        raise HTTPException(status_code=502, detail=str(e))
    sync_state.set_target(db, "anki", {"last_sync": core.now_iso(), "url": url})
    _audit("sync.anki", target=req.deck,
           detail=f"added={result.get('added',0)}", feature="sync_anki")
    return result


# ---------------------------------------------------------------------------
# Study planner (§6) - assessments, calendar, spaced repetition, progress.
# All deterministic; `course` defaults to the active course.
# ---------------------------------------------------------------------------


def _course_or_active(course_id: Optional[int]) -> int:
    cid = course_id if course_id is not None else settings_store.get_active_course(db)
    if cid is None:
        raise HTTPException(status_code=400, detail="No active course; create one first.")
    return cid


@app.get("/api/assessments")
def api_assessments_list(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(db)
    return {"assessments": study_planner.list_assessments(db, cid)}


@app.post("/api/assessments")
def api_assessments_create(req: AssessmentReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    try:
        return study_planner.create_assessment(db, cid, req.name, req.due_date,
                                               req.weight, req.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/assessments/{assessment_id}")
def api_assessments_update(assessment_id: int, req: AssessmentUpdate) -> Dict[str, Any]:
    if not db.get_assessment(assessment_id):
        raise HTTPException(status_code=404, detail="Assessment not found")
    try:
        return study_planner.update_assessment(db, assessment_id,
                                               **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/assessments/{assessment_id}")
def api_assessments_delete(assessment_id: int) -> Dict[str, Any]:
    if not study_planner.delete_assessment(db, assessment_id):
        raise HTTPException(status_code=404, detail="Assessment not found")
    return {"deleted": assessment_id}


@app.get("/api/plan")
def api_plan(course: Optional[int] = None, horizon: int = 14,
            hours: float = 10.0) -> Dict[str, Any]:
    cid = _course_or_active(course)
    return study_planner.generate_plan(db, OUTPUT_DIR, cid, horizon_days=horizon,
                                      hours_per_week=hours)


@app.get("/api/calendar.ics")
def api_calendar(course: Optional[int] = None) -> Response:
    cid = _course_or_active(course)
    row = db.get_course(cid)
    name = (row["code"] or row["name"]) if row else "Course"
    ics = study_planner.build_ics(db, OUTPUT_DIR, cid, course_name=name)
    return Response(content=ics, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="course-{cid}.ics"'})


@app.post("/api/study-sessions")
def api_study_session(req: StudySessionReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    sid = db.log_study_session(cid, core.now_iso(), req.duration, req.activity_type)
    return {"id": sid, "course_id": cid, "duration": req.duration}


@app.get("/api/reviews")
def api_reviews(course: Optional[int] = None, due: str = "") -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(db)
    return {"reviews": study_planner.due_reviews(db, cid, due or None)}


@app.post("/api/reviews/{item_id}/grade")
def api_review_grade(item_id: int, req: GradeReq) -> Dict[str, Any]:
    out = study_planner.grade_review(db, item_id, req.quality)
    if out is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return out


@app.post("/api/quiz-attempts")
def api_quiz_attempt(req: QuizAttemptReq) -> Dict[str, Any]:
    cid = _course_or_active(req.course_id)
    aid = db.record_quiz_attempt(cid, req.scope, req.score, req.total, req.mode)
    return {"id": aid, "course_id": cid, "score": req.score, "total": req.total}


@app.get("/api/progress")
def api_progress(course: Optional[int] = None) -> Dict[str, Any]:
    cid = _course_or_active(course)
    return study_planner.progress(db, cid)


# ---------------------------------------------------------------------------
# v3 study toolkit - streak, next-up, glossary, keywords, workload, study
# guide, citations, offline practice quiz, notes & user tags. All local; the
# text features are dependency-free (no AI model required).
# ---------------------------------------------------------------------------


def _active_course_name() -> str:
    cid = settings_store.get_active_course(db)
    if cid is None:
        return ""
    row = db.get_course(cid)
    return (row["code"] or row["name"]) if row else ""


@app.get("/api/streak")
def api_streak(course: Optional[int] = None,
               goal: int = streak_mod.DEFAULT_GOAL_MINUTES) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(db)
    return streak_mod.compute(db, course_id=cid, goal_minutes=goal)


@app.get("/api/next-up")
def api_next_up(course: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(db)
    return nextup.compute(db, OUTPUT_DIR, course_id=cid)


@app.get("/api/glossary")
def api_glossary(course: Optional[str] = None) -> Dict[str, Any]:
    name = course if course is not None else _active_course_name()
    return glossary.build_glossary(OUTPUT_DIR, course=name)


@app.post("/api/export/glossary")
def api_export_glossary(req: ExportNamedRequest) -> Dict[str, Any]:
    name = req.course or _active_course_name()
    result = glossary.write_glossary(OUTPUT_DIR, course=name)
    if result["count"] == 0:
        raise HTTPException(status_code=404,
                            detail="No terms found yet. Transcribe some lectures first.")
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        _copy_export_files([result["markdown"]], OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@app.get("/api/keywords")
def api_keywords(limit: int = 30) -> Dict[str, Any]:
    text = "\n".join(lec.get("text", "") for lec in lectures.iter_lectures(OUTPUT_DIR))
    return {"keywords": keywords.keywords(text, limit=limit),
            "phrases": keywords.key_phrases(text, limit=limit)}


@app.get("/api/workload")
def api_workload(read_wpm: int = workload.READ_WPM,
                 review_wpm: int = workload.REVIEW_WPM) -> Dict[str, Any]:
    return workload.estimate(OUTPUT_DIR, read_wpm=read_wpm, review_wpm=review_wpm)


@app.get("/api/study-guide")
def api_study_guide(course: Optional[str] = None) -> Dict[str, Any]:
    name = course if course is not None else _active_course_name()
    return studyguide.build_markdown(OUTPUT_DIR, course=name)


@app.post("/api/export/study-guide")
def api_export_study_guide(req: ExportNamedRequest) -> Dict[str, Any]:
    name = req.course or _active_course_name()
    result = studyguide.write_guide(OUTPUT_DIR, course=name)
    if result["lectures"] == 0:
        raise HTTPException(status_code=404,
                            detail="Nothing to build yet. Transcribe some lectures first.")
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        _copy_export_files([result["path"]], OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@app.get("/api/citations")
def api_citations(path: str) -> Dict[str, Any]:
    for g in core.list_transcripts(OUTPUT_DIR):
        if not core._is_transcript_group(g):
            continue
        if path != g.get("folder", "") + "/" + g["stem"] and \
                path not in g["formats"].values():
            continue
        meta = lectures.lecture_meta(OUTPUT_DIR, g)
        if not meta.get("course"):
            meta["course"] = _active_course_name()
        return {"path": path, "title": meta["title"],
                "citations": citations.cite_all(meta)}
    raise HTTPException(status_code=404, detail="Lecture not found")


@app.get("/api/practice-quiz")
def api_practice_quiz(course: Optional[int] = None, count: int = 10,
                      choices: int = 4, seed: Optional[int] = None) -> Dict[str, Any]:
    cid = course if course is not None else settings_store.get_active_course(db)
    return practice.from_db(db, course_id=cid, count=count, choices=choices, seed=seed)


@app.post("/api/practice-quiz/grade")
def api_practice_grade(req: PracticeGradeReq) -> Dict[str, Any]:
    result = practice.grade(req.questions, req.answers)
    if req.record:
        cid = req.course_id if req.course_id is not None \
            else settings_store.get_active_course(db)
        if cid is not None:
            db.record_quiz_attempt(cid, "practice", result["score"],
                                   result["total"], "practice")
    return result


# -- notes & bookmarks ------------------------------------------------------

def _note_dict(row) -> Dict[str, Any]:
    return {"id": row["id"], "path": row["path"], "body": row["body"],
            "timestamp_s": row["timestamp_s"], "bookmark": bool(row["bookmark"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


@app.get("/api/notes")
def api_notes_list(path: Optional[str] = None,
                   course: Optional[int] = None) -> Dict[str, Any]:
    rows = db.list_notes(path=path, course_id=course)
    return {"notes": [_note_dict(r) for r in rows]}


@app.post("/api/notes")
def api_notes_create(req: NoteReq) -> Dict[str, Any]:
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Note body is required.")
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(db)
    nid = db.add_note(req.path, req.body.strip(), course_id=cid,
                      timestamp_s=req.timestamp_s, bookmark=req.bookmark)
    row = db.query_one("SELECT * FROM notes WHERE id=?", (nid,))
    return _note_dict(row)


@app.patch("/api/notes/{note_id}")
def api_notes_update(note_id: int, req: NoteUpdate) -> Dict[str, Any]:
    if not db.update_note(note_id, **req.model_dump(exclude_none=True)):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"updated": note_id}


@app.delete("/api/notes/{note_id}")
def api_notes_delete(note_id: int) -> Dict[str, Any]:
    if not db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": note_id}


# -- user tags --------------------------------------------------------------

@app.get("/api/tags")
def api_tags_list(path: Optional[str] = None) -> Dict[str, Any]:
    if path is not None:
        return {"path": path, "tags": db.tags_for_path(path)}
    return {"tags": [{"name": r["name"], "count": r["n"], "color": r["color"]}
                     for r in db.list_tags()]}


@app.post("/api/tags")
def api_tags_add(req: ItemTagReq) -> Dict[str, Any]:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required.")
    cid = req.course_id if req.course_id is not None \
        else settings_store.get_active_course(db)
    db.add_item_tag(req.path, name, course_id=cid)
    return {"path": req.path, "tags": db.tags_for_path(req.path)}


@app.delete("/api/tags")
def api_tags_remove(path: str, name: str) -> Dict[str, Any]:
    db.remove_item_tag(path, name)
    db.prune_unused_tags()
    return {"path": path, "tags": db.tags_for_path(path)}


# ---------------------------------------------------------------------------
# Security & privacy (§10) - secret storage (names only over the wire), data
# transparency labels, and an audit trail of anything that left the machine.
# ---------------------------------------------------------------------------


def _audit(action: str, target: str = "", detail: str = "", feature: str = "") -> None:
    """Record an external/cloud action so the user can review what left the box."""
    try:
        db.add_audit(action, target=target, detail=detail,
                    label=secret_store.label_for(feature) if feature else "")
    except Exception:
        pass


@app.get("/api/secrets")
def api_secrets_list() -> Dict[str, Any]:
    return {"backend": secret_store.backend_status(),
            "names": secret_store.list_secret_names(OUTPUT_DIR)}


@app.put("/api/secrets/{name}")
def api_secrets_set(name: str, req: SecretReq) -> Dict[str, Any]:
    if not req.value.strip():
        raise HTTPException(status_code=400, detail="Empty secret.")
    secret_store.set_secret(name, req.value, root=OUTPUT_DIR)
    _audit("secret.set", target=name, feature="")
    return {"stored": name, "backend": secret_store.backend_status()["backend"]}


@app.delete("/api/secrets/{name}")
def api_secrets_delete(name: str) -> Dict[str, Any]:
    ok = secret_store.delete_secret(name, root=OUTPUT_DIR)
    return {"deleted": name if ok else "", "ok": ok}


@app.post("/api/secrets/clear")
def api_secrets_clear() -> Dict[str, Any]:
    secret_store.clear_all(OUTPUT_DIR)
    _audit("secret.clear_all")
    return {"cleared": True}


@app.get("/api/privacy")
def api_privacy() -> Dict[str, Any]:
    return {"transparency": secret_store.transparency(),
            "secrets": secret_store.backend_status()}


@app.get("/api/audit")
def api_audit(limit: int = 200) -> Dict[str, Any]:
    rows = db.list_audit(limit)
    return {"events": [dict(r) for r in rows]}


@app.post("/api/audit/clear")
def api_audit_clear() -> Dict[str, Any]:
    return {"cleared": db.clear_audit()}


# ---------------------------------------------------------------------------
# Export engine (§9) - preset-driven, scoped, preview-first.
# ---------------------------------------------------------------------------


@app.get("/api/export/presets")
def api_export_presets() -> Dict[str, Any]:
    return {"presets": export_engine.PRESET_TARGETS, "targets": export_engine.ALL_TARGETS,
            "scopes": list(export_engine.SCOPES)}


@app.post("/api/export/preview")
def api_export_preview(req: ExportReq) -> Dict[str, Any]:
    try:
        return export_engine.preview(OUTPUT_DIR, preset=req.preset, target=req.target,
                                     scope=req.scope, scope_target=req.scope_target,
                                     course=req.course)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/export/run")
def api_export_run(req: ExportReq) -> Dict[str, Any]:
    try:
        out = export_engine.export(OUTPUT_DIR, preset=req.preset, target=req.target,
                                  scope=req.scope, scope_target=req.scope_target,
                                  course=req.course, db=db,
                                  course_id=settings_store.get_active_course(db))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


# ---------------------------------------------------------------------------
# Analytics & local feedback (§13) - computed from local rows; never phones home.
# ---------------------------------------------------------------------------


@app.get("/api/analytics")
def api_analytics(course: Optional[int] = None) -> Dict[str, Any]:
    stats = analytics.compute(db, course)
    stats["feedback_prompt"] = analytics.feedback_prompt(stats)
    return stats


@app.post("/api/analytics/export")
def api_analytics_export() -> Dict[str, Any]:
    """User-initiated, anonymised diagnostics JSON (no secrets/PII, never auto-sent)."""
    return analytics.diagnostics_export(db, OUTPUT_DIR,
                                       settings_store.get_active_course(db))


# ---------------------------------------------------------------------------
# Packaging & recovery (§11) - environment checker + portable backup/restore.
# ---------------------------------------------------------------------------


class RestoreReq(BaseModel):
    path: str
    overwrite: bool = False


@app.get("/api/environment")
def api_environment() -> Dict[str, Any]:
    """What this machine can do (present/missing engines + deps + disk)."""
    return backup_mod.environment_report(OUTPUT_DIR)


@app.post("/api/backup")
def api_backup() -> Dict[str, Any]:
    """Zip the DB + whole library into one portable file (secrets excluded)."""
    return backup_mod.create_backup(OUTPUT_DIR)


@app.post("/api/restore")
def api_restore(req: RestoreReq) -> Dict[str, Any]:
    """Unpack a backup into the library (safe merge unless overwrite=true)."""
    try:
        result = backup_mod.restore_backup(Path(req.path).expanduser(), OUTPUT_DIR,
                                          overwrite=req.overwrite)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Backup not found: {e}")
    # The DB file is held open here; a full DB replace (overwrite=true) only takes
    # effect on the next launch, which migrates it forward automatically.
    result["restart_required_for_db"] = req.overwrite
    return result


@app.post("/api/feed")
def api_feed(req: FeedRequest) -> Dict[str, Any]:
    try:
        items = core.parse_feed(req.source, cookies=req.cookies)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse feed: {e}")
    return {"count": len(items), "lectures": [it.to_dict() for it in items]}


@app.post("/api/moodle/panopto-feed")
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


class PanoptoDownloadRequest(BaseModel):
    lectures: List[Dict[str, Any]]    # recording dicts from /api/moodle/panopto-feed
    output_dir: str                   # folder to save the videos in
    cookies: str = ""


@app.post("/api/panopto/download")
def api_panopto_download(req: PanoptoDownloadRequest) -> Dict[str, Any]:
    """Download Panopto recordings (video) into a folder, no transcription.

    Used for the "lecture recording without transcript" path. Prefers each
    lecture's ``video_url`` (mp4), falling back to ``url``.
    """
    dest = Path(req.output_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    downloaded: List[str] = []
    failed: List[str] = []
    for lec in req.lectures:
        stem = core.safe_name(lec.get("title") or lec.get("safe_title") or "recording")
        url = (lec.get("video_url") or lec.get("url") or "").strip()
        if not url:
            failed.append(stem)
            continue
        try:
            transcribe.download_media(url, dest / f"{stem}.mp4", cookies=req.cookies)
            downloaded.append(stem)
        except Exception:
            failed.append(stem)
    return {"downloaded": len(downloaded), "failed": failed, "output_dir": str(dest)}


@app.post("/api/feed/upload")
async def api_feed_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    raw = await file.read()
    try:
        items = core.parse_feed_bytes(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse XML: {e}")
    return {
        "count": len(items),
        "channel": core.channel_title(raw),
        "lectures": [it.to_dict() for it in items],
    }


@app.get("/api/transcripts")
def api_transcripts() -> Dict[str, Any]:
    return {"output_dir": str(OUTPUT_DIR), "items": core.list_transcripts(OUTPUT_DIR)}


@app.get("/api/library")
def api_library() -> Dict[str, Any]:
    """Everything in the library, categorised (transcripts, documents, Notion,
    generated exports, and any other source files)."""
    return core.list_library(OUTPUT_DIR)


@app.get("/api/transcript")
def api_transcript(path: str) -> Dict[str, Any]:
    try:
        content = core.read_transcript_file(OUTPUT_DIR, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Transcript not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"path": path, "content": content}


@app.get("/api/search")
def api_search(q: str, week: Optional[int] = None, type: str = "",
               fuzzy: bool = True) -> Dict[str, Any]:
    """Full-text search with optional metadata filters + a fuzzy title fallback (§2)."""
    return {"query": q, "results": search.search(OUTPUT_DIR, q, week=week, ftype=type,
                                                 fuzzy=fuzzy)}


@app.get("/api/index")
def api_index(week: Optional[int] = None, type: str = "", tag: str = "",
              q: str = "", sort: str = "date") -> Dict[str, Any]:
    """Unified, filterable/sortable library index (§2): sort by date/name/week,
    filter by week/type/tag, tag-aware search."""
    return search.library_view(OUTPUT_DIR, week=week, ftype=type, tag=tag, q=q, sort=sort)


@app.get("/api/related")
def api_related(path: str) -> Dict[str, Any]:
    return {"path": path, "related": search.related(OUTPUT_DIR, path)}


@app.get("/api/views")
def api_views_list() -> Dict[str, Any]:
    active = settings_store.get_active_course(db)
    saved = [
        {"id": v["id"], "name": v["name"], "builtin": False,
         "query": _json_loads(v["query_json"])}
        for v in db.list_saved_views(active)
    ]
    return {"views": search.BUILTIN_VIEWS + saved}


@app.post("/api/views")
def api_views_create(req: SavedViewCreate) -> Dict[str, Any]:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="View name is required.")
    vid = db.create_saved_view(req.name.strip(), json.dumps(req.query or {}),
                              course_id=settings_store.get_active_course(db))
    return {"id": vid, "name": req.name.strip(), "builtin": False, "query": req.query or {}}


@app.delete("/api/views/{view_id}")
def api_views_delete(view_id: int) -> Dict[str, Any]:
    if not db.delete_saved_view(view_id):
        raise HTTPException(status_code=404, detail="Saved view not found")
    return {"deleted": view_id}


def _json_loads(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


@app.post("/api/export/notebooklm")
def api_export_notebooklm(req: NotebookLMRequest) -> Dict[str, Any]:
    """Render existing transcripts into clean, NotebookLM-friendly Markdown."""
    result = core.export_notebooklm(
        OUTPUT_DIR,
        selection=req.selection,
        combined=req.combined,
        course=req.course,
    )
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="No transcripts found to export. Transcribe some lectures first.",
        )
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        files = list(result.get("files", []))
        if result.get("combined"):
            files.append(result["combined"])
        _copy_export_files(files, OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@app.post("/api/export/all")
def api_export_all(req: ExportAllRequest) -> Dict[str, Any]:
    """Bring everything imported (transcripts + documents + Notion) together as
    one NotebookLM / AI export, with an optional combined everything_pack.md."""
    result = core.export_all_sources(OUTPUT_DIR, combined=req.combined, course=req.course)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to export yet. Import some lectures, documents or "
            "Notion pages first.",
        )
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        files = []
        if result.get("combined"):
            files.append(result["combined"])
        _copy_export_files(files, OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
    return result


@app.post("/api/export/formats")
def api_export_formats(req: FormatsRequest) -> Dict[str, Any]:
    """Generate subtitles / alternate output formats from existing transcripts."""
    result = core.export_formats(OUTPUT_DIR, req.formats, interval=req.interval)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to generate. Transcribe some lectures first, and pick "
            "at least one format.",
        )
    return result


@app.post("/api/export/srt")
def api_export_srt(req: SrtExportRequest) -> Dict[str, Any]:
    """Generate SRT subtitle files from all transcribed lectures and optionally copy
    them to a folder alongside the video recordings so players pick them up automatically.

    SRT files share the same stem as each lecture's transcript so subtitle players can
    auto-load them when the video and .srt live in the same folder."""
    result = core.export_formats(OUTPUT_DIR, ["srt"])
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="No transcripts found. Transcribe some lectures first - SRT files "
            "are generated from the timing data produced during transcription.",
        )
    if req.output_dir:
        dest = Path(req.output_dir).expanduser()
        _copy_export_files(result.get("files", []), OUTPUT_DIR, dest)
        result["output_dir"] = str(dest)
        result["dest"] = str(dest)
        if req.include_recordings:
            result["recordings"] = _export_recordings(dest)
    return result


_STUDY_SUMMARY_SYSTEM = (
    "You are a concise study assistant. Summarise this lecture into 2–3 sentences of "
    "clean, accurate study notes, grounded strictly in the provided text. No preamble, "
    "no markdown, no bullet points - just the sentences."
)


def _study_summarizer(cfg: Dict[str, Any]):
    """Build an LLM-backed Summary-column writer for the Notion CSV, falling back to
    the extractive cell when the model is unreachable or the text is empty."""
    def summarize(output_dir, group, json_text: str) -> str:
        text = (json_text or "").strip()
        if not text:
            return study._summary_cell(output_dir, group, json_text)
        try:
            out = llm.complete(f"Summarise this lecture in 2–3 sentences:\n\n{text[:12000]}",
                               system=_STUDY_SUMMARY_SYSTEM, config=cfg)
            if out.strip():
                return " ".join(out.split())
        except llm.LLMError:
            pass
        return study._summary_cell(output_dir, group, json_text)
    return summarize


@app.post("/api/export/notion-csv")
def api_export_notion_csv(req: StudyCsvRequest) -> Dict[str, Any]:
    """Export the transcript library as a Notion-importable study-database CSV.

    When an LLM provider is configured the Summary column is AI-written and the
    job runs in the background so the UI stays responsive. Extractive mode is
    synchronous."""
    cid = settings_store.get_active_course(db)
    cfg = llm.get_config(db, cid)
    used_ai = llm.is_enabled(cfg)
    course = req.course
    filename = req.filename
    out_dir_str = req.output_dir
    captured_cfg = dict(cfg)

    if used_ai:
        def work(_progress):
            _progress("Writing study database with AI summaries...")
            result = study.write_study_database(
                OUTPUT_DIR, course=course, filename=filename,
                summarizer=_study_summarizer(captured_cfg))
            if out_dir_str:
                _copy_export_files([result.get("csv", "")], OUTPUT_DIR,
                                   Path(out_dir_str).expanduser())
                result["output_dir"] = str(Path(out_dir_str).expanduser())
            result["generated"] = "ai"
            result["provider"] = captured_cfg.get("provider")
            if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
                _audit("ai.study_csv", target=captured_cfg.get("provider", ""),
                       detail="Notion study CSV summaries", feature="ai_cloud")
            return result
        job = manager.submit("Study database (AI summaries)", work,
                             type="study_csv", payload=req.model_dump(), course_id=cid)
        return job.to_dict()

    result = study.write_study_database(OUTPUT_DIR, course=course, filename=filename)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to export yet. Transcribe or convert some lectures first.",
        )
    if out_dir_str:
        _copy_export_files([result.get("csv", "")], OUTPUT_DIR,
                           Path(out_dir_str).expanduser())
        result["output_dir"] = str(Path(out_dir_str).expanduser())
    result["generated"] = "extractive"
    return result


@app.post("/api/flashcards/generate")
def api_flashcards_generate(req: FlashcardGenRequest) -> Dict[str, Any]:
    """Generate Anki-importable flashcards using LLM (requires Ollama/configured provider).

    Runs as a background job so the UI stays responsive. Returns job status immediately."""
    cid = settings_store.get_active_course(db)
    cfg = llm.get_config(db, cid)
    if not llm.is_enabled(cfg):
        raise HTTPException(
            status_code=503,
            detail="LLM not available. Install Ollama with llama3 to generate flashcards. "
            "See the Extra Dependencies installer to set this up.")
    selection = req.selection
    course = req.course
    deck = req.deck
    max_cards = max(1, min(req.max_cards, 200))
    out_dir_str = req.output_dir
    captured_cfg = dict(cfg)

    def work(_progress):
        _progress("Generating flashcards with LLM...")
        out = ai.generate_flashcards(OUTPUT_DIR, selection=selection, course=course,
                                     max_cards=max_cards, config=captured_cfg)
        cards = out.get("cards", [])
        if not cards:
            raise ValueError("No flashcards generated - try adding more transcript content.")
        if course:
            ctag = flashcards._tag(course)
            if ctag:
                for c in cards:
                    tags = list(c.get("tags") or [])
                    if ctag not in tags:
                        tags.insert(0, ctag)
                    c["tags"] = tags
        result = flashcards.write_deck(OUTPUT_DIR, cards, deck)
        result["course"] = course
        result["generated"] = out.get("generated")
        result["provider"] = out.get("provider")
        if out_dir_str:
            dest = Path(out_dir_str).expanduser()
            _copy_export_files([result.get("anki_tsv", ""), result.get("csv", "")],
                               OUTPUT_DIR, dest / "_flashcards")
            result["output_dir"] = str(dest)
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.flashcards", target=captured_cfg.get("provider", ""),
                   detail="flashcard export", feature="ai_cloud")
        return result

    job = manager.submit(f"Flashcards: {deck}", work,
                        type="flashcards_generate", payload=req.model_dump(), course_id=cid)
    return job.to_dict()


@app.post("/api/flashcards/categorize")
def api_flashcards_categorize(req: FlashcardCatRequest) -> Dict[str, Any]:
    """Tag an existing flashcard deck by topic using LLM (requires configured provider).

    Runs as a background job. Accepts pasted CSV/TSV text or a file path."""
    cid = settings_store.get_active_course(db)
    cfg = llm.get_config(db, cid)
    if not llm.is_enabled(cfg):
        raise HTTPException(
            status_code=503,
            detail="LLM not available. Install Ollama with llama3 to categorize flashcards.")
    text = req.text
    if not text and req.path:
        try:
            text = core.read_any_text(Path(req.path).expanduser())
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    cards = flashcards.parse_cards_text(text)
    if not cards:
        raise HTTPException(status_code=400, detail="No flashcards found in the input "
                            "(expected CSV/TSV with front, back[, tags]).")
    course = req.course
    deck = req.deck
    captured_cfg = dict(cfg)

    def work(_progress):
        _progress("Categorizing flashcards with LLM...")
        out = ai.llm_categorize_cards(cards, course=course, config=captured_cfg)
        tagged = out.get("cards", cards)
        result = flashcards.write_deck(OUTPUT_DIR, tagged, deck)
        result["course"] = course
        result["generated"] = out.get("generated")
        result["provider"] = out.get("provider")
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.flashcards_cat", target=captured_cfg.get("provider", ""),
                   detail="flashcard categorize", feature="ai_cloud")
        return result

    job = manager.submit(f"Categorize deck: {deck}", work,
                        type="flashcards_categorize", payload=req.model_dump(), course_id=cid)
    return job.to_dict()


@app.post("/api/export/cheatsheet")
def api_export_cheatsheet(req: CheatsheetRequest) -> Dict[str, Any]:
    """Build a dense exam cheat sheet PDF from the course material, condensed by the
    LLM and bounded to a maximum number of A4 pages. Runs as a background job."""
    cid = settings_store.get_active_course(db)
    cfg = llm.get_config(db, cid)
    if not llm.is_enabled(cfg):
        raise HTTPException(
            status_code=503,
            detail="An AI model is required to build a cheat sheet. Start Ollama (Export "
            "tab) or configure a provider, then try again.")
    course = req.course
    max_pages = max(1, min(req.max_pages, 10))
    save_path = req.save_path
    captured_cfg = dict(cfg)

    def work(_progress):
        _progress("Condensing course material...", 0.2)
        result = cheatsheet_mod.build(OUTPUT_DIR, course=course, max_pages=max_pages,
                                      config=captured_cfg, save_path=save_path)
        _progress("done", 1.0)
        if captured_cfg.get("provider") in llm.CLOUD_PROVIDERS:
            _audit("ai.cheatsheet", target=captured_cfg.get("provider", ""),
                   detail="exam cheat sheet", feature="ai_cloud")
        return result

    job = manager.submit(f"Exam cheat sheet ({max_pages} page{'s' if max_pages != 1 else ''})",
                         work, type="cheatsheet", payload=req.model_dump(), course_id=cid)
    return job.to_dict()


@app.post("/api/transcribe")
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
                         course_id=settings_store.get_active_course(db))
    return job.to_dict()


# --- Job factories: rebuild a job's work fn from its persisted payload so §3
# retry can re-run it after a restart, when the original closure is long gone.


def _make_transcribe_work(payload: Dict[str, Any]):
    lecture = payload.get("lecture", {})
    item = core.LectureItem(
        title=lecture.get("title", "lecture"),
        url=lecture.get("url", ""),
        size=int(lecture.get("size", 0) or 0),
        duration=int(lecture.get("duration", 0) or 0),
        pub_date=lecture.get("pub_date", ""),
        author=lecture.get("author", ""),
        guid=lecture.get("guid", ""),
    )

    def work(progress) -> Dict[str, Any]:
        return transcribe.transcribe_lecture(
            item, OUTPUT_DIR,
            engine=payload.get("engine", "faster-whisper"),
            model=payload.get("model", "small"),
            language=payload.get("language", "en"),
            device=payload.get("device", "auto"),
            organize=payload.get("organize", "auto"),
            outputs=payload.get("outputs", ["txt", "md", "json", "summary"]),
            interval=payload.get("interval", 30),
            keep_media=payload.get("keep_media", False),
            audio_only=payload.get("audio_only", False),
            skip_existing=payload.get("skip_existing", True),
            force=payload.get("force", False),
            cookies=payload.get("cookies", ""),
            course=payload.get("course", ""),
            video_url=lecture.get("video_url", ""),
            progress=progress,
        )

    return work


# type -> factory(payload) -> work(progress). Extend as new long-running job
# types are added (imports, exports, sync, …).
JOB_FACTORIES = {"transcribe": _make_transcribe_work}


@app.post("/api/organize")
def api_organize(req: OrganizeRequest) -> Dict[str, Any]:
    """Move existing transcripts into none/date/week/topic folders."""
    if req.by not in core.ORG_CHOICES:
        raise HTTPException(status_code=400, detail=f"organize must be one of {core.ORG_CHOICES}")
    moved = core.reorganize_outputs(OUTPUT_DIR, req.by)
    return {"moved": len(moved), "files": moved, "by": req.by}


@app.get("/api/jobs")
def api_jobs() -> Dict[str, Any]:
    return {"jobs": manager.list()}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/logs")
def api_job_logs(job_id: str) -> Dict[str, Any]:
    logs = manager.logs(job_id)
    if logs is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"id": job_id, "logs": logs}


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str) -> Dict[str, Any]:
    if not manager.get(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    if not manager.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job already finished - nothing to cancel.")
    return {"id": job_id, "canceled": True}


@app.post("/api/jobs/{job_id}/retry")
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


@app.post("/api/pdf/convert")
def api_pdf_convert(req: PdfRequest) -> Dict[str, Any]:
    path = Path(req.input_path).expanduser()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.input_path}")
    try:
        converted = core.convert_pdf_tree(
            path,
            suffix=req.suffix,
            include_subfolders=req.include_subfolders,
            overwrite=req.overwrite,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "count": len(converted),
        "output_root": str(path.parent / f"{path.name}{req.suffix}"),
        "files": [{"pdf": p, "md": m} for p, m in converted],
    }


@app.post("/api/moodle/parse")
def api_moodle_parse(req: MoodleRequest) -> Dict[str, Any]:
    """Parse a Moodle course HTML export into a structured outline."""
    try:
        parsed = sources.parse_moodle_course(Path(req.path).expanduser())
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse course page: {e}")
    if req.save_outline:
        parsed["saved_as"] = sources.save_outline(OUTPUT_DIR, parsed)
    return parsed


@app.post("/api/moodle/import-url")
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
        parsed["saved_as"] = sources.save_outline(OUTPUT_DIR, parsed)
    if req.create_course and (parsed.get("title") or parsed.get("code")):
        course = courses.create_course(db, name=parsed.get("title") or parsed["code"],
                                      code=parsed.get("code", ""))
        courses.set_active(db, course["id"])
        parsed["course"] = course
    return parsed


@app.post("/api/moodle/fetch-course")
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

    sources.save_outline(OUTPUT_DIR, parsed)

    # Only download + convert documents when the user ticked "Other docs".
    downloaded = {"downloaded": 0, "errors": []}
    converted = None
    if req.grab_docs:
        res_dir = OUTPUT_DIR / "_resources"
        try:
            downloaded = moodle_resources.download_resources(
                parsed.get("activities", []), res_dir, cookies=req.cookies)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Resource download failed: {e}")
        if req.convert and downloaded["downloaded"]:
            converted = core.convert_documents(res_dir, OUTPUT_DIR, target="ai",
                                              combined=True, keep_images=req.keep_images)
    _audit("moodle.fetch_course", target=parsed.get("code", ""),
           detail=f"resources={downloaded['downloaded']}", feature="moodle_import_url")

    exported = None
    if req.export == "notebooklm":
        exported = core.export_notebooklm(OUTPUT_DIR, combined=True,
                                         course=parsed.get("code", ""))
    elif req.export == "all":
        exported = core.export_all_sources(OUTPUT_DIR, combined=True,
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


def _moodle_token_name(host: str) -> str:
    return f"moodle_token:{host}"


def _sso_provider_label(host: str) -> str:
    """Friendly name for a detected SSO identity-provider host."""
    h = (host or "").lower()
    if "microsoft" in h or "live.com" in h:
        return "Microsoft"
    if "google" in h:
        return "Google"
    if "okta" in h:
        return "Okta"
    return "single sign-on (SSO)"


def _save_api_outline(model: Dict[str, Any]) -> str:
    """Write the labelled course outline as an AI/NotebookLM source. Returns rel path."""
    core.ensure_dir(OUTPUT_DIR)
    c = model["course"]
    stem = core.safe_name(c.get("code") or c.get("fullname") or "course") + "_outline"
    target = OUTPUT_DIR / f"{stem}.md"
    target.write_text(model["outline_markdown"], encoding="utf-8")
    return target.relative_to(OUTPUT_DIR).as_posix()


_MOODLE_PASSPORT = "courseassistant"


@app.get("/api/moodle/launch-url")
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


class DecodeLaunchTokenReq(BaseModel):
    raw: str   # full moodlemobile://token=... URL pasted from the browser address bar


@app.post("/api/moodle/decode-launch-token")
def api_moodle_decode_launch_token(req: DecodeLaunchTokenReq) -> Dict[str, Any]:
    """Decode the moodlemobile:// redirect URL that Moodle issues after a successful
    browser SSO login and return the web-service token (see moodle_api for format)."""
    try:
        token = moodle_api.decode_launch_token(req.raw, expected_passport=_MOODLE_PASSPORT)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": token}


class SsoCallbackReq(BaseModel):
    raw: str   # full courseassistant://token=… URL sent by the OS protocol handler


@app.post("/api/moodle/sso-callback")
def api_moodle_sso_callback(req: SsoCallbackReq) -> Response:
    """Receives courseassistant://token=… from the Windows protocol handler and
    stores the decoded token for the next poll."""
    try:
        token = moodle_api.decode_launch_token(req.raw, expected_passport=_MOODLE_PASSPORT)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sso_protocol.store_token(token)
    return Response(status_code=204)


from fastapi import Request as _Request  # noqa: E402 - local import to avoid name collision


@app.get("/api/moodle/sso-poll")
def api_moodle_sso_poll(request: _Request) -> Dict[str, Any]:
    """Poll for a token delivered by the OS protocol handler.  Also re-registers
    the handler with the correct port on the first call (covers dev-server restarts)."""
    port = request.url.port or int(os.environ.get("CA_PORT", "8123"))
    sso_protocol.register(port)
    return {"token": sso_protocol.poll_token()}


@app.post("/api/moodle/connect")
def api_moodle_connect(req: MoodleConnectReq) -> Dict[str, Any]:
    """Connect to a Moodle site through its official mobile web-service API and
    list the courses you're enrolled in. Authenticate by username+password (where
    the site allows the token grant) or by pasting a web-service token (SSO sites).

    The token is stored in the local encrypted secrets store keyed by host, so the
    follow-up import never has to round-trip credentials. Replaces the old
    browser-cookie / HTML-scraping path with exact, typed course data."""
    try:
        base = moodle_api.normalize_base_url(req.url)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    host = urlparse(base).hostname or ""
    token = req.token.strip()
    try:
        if not token:
            if not (req.username and req.password):
                raise HTTPException(
                    status_code=400,
                    detail="Enter your Moodle username and password, or paste a "
                           "web-service token from the Moodle mobile app.")
            try:
                token = moodle_api.fetch_token(base, req.username, req.password)
            except moodle_api.MoodleApiError:
                # A generic "invalid login" on an SSO-fronted site usually means
                # the password grant is disabled, not a wrong password. Detect the
                # external IdP and steer the user to the browser token flow.
                provider = moodle_sso.detect_sso(base)
                if provider:
                    raise moodle_api.MoodleApiError(
                        "SSO_REJECTED: This site signs in through "
                        f"{_sso_provider_label(provider)} - username/password can't be "
                        "used here. Use ‘Sign in via browser’ below to get your token."
                    ) from None
                raise
        client = moodle_api.MoodleClient(base, token)
        info = client.site_info()
        courses_list = client.list_courses(info.get("userid"))
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))

    secret_store.set_secret(_moodle_token_name(host), token, root=OUTPUT_DIR)
    _audit("moodle.connect", target=host, feature="moodle_import_url")
    return {
        "host": host,
        "base_url": base,
        "sitename": info.get("sitename", ""),
        "fullname": info.get("fullname", ""),
        "courses": courses_list,
    }


@app.post("/api/moodle/api-import")
def api_moodle_api_import(req: MoodleApiImportReq) -> Dict[str, Any]:
    """Import one course through the web-service API: fetch the typed content tree,
    label every item (lecture / document / link / activity) with 100% fidelity,
    download document files under their exact names, convert them to Markdown, save
    the outline, and surface lecture feeds for transcription. Requires a prior
    ``/api/moodle/connect``."""
    try:
        base = moodle_api.normalize_base_url(req.url)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    host = urlparse(base).hostname or ""
    token = secret_store.get_secret(_moodle_token_name(host), root=OUTPUT_DIR)
    if not token:
        raise HTTPException(
            status_code=400,
            detail=f"Not connected to {host or 'this Moodle site'} yet - connect first.")

    client = moodle_api.MoodleClient(base, token)
    try:
        model = moodle_api.import_course(client, req.course_id)
    except moodle_api.MoodleApiError as e:
        raise HTTPException(status_code=400, detail=str(e))

    outline_rel = _save_api_outline(model)

    downloaded = {"downloaded": 0, "files": [], "errors": []}
    converted = None
    if req.grab_docs and model["documents"]:
        res_dir = OUTPUT_DIR / "_resources"
        try:
            downloaded = moodle_api.download_documents(client, model["documents"], res_dir)
        except moodle_api.MoodleApiError as e:
            raise HTTPException(status_code=502, detail=f"Document download failed: {e}")
        if req.convert and downloaded["downloaded"]:
            converted = core.convert_documents(
                res_dir, OUTPUT_DIR, target="ai", combined=True, keep_images=req.keep_images)

    course_rec = None
    if req.create_course and (model["course"]["fullname"] or model["course"]["code"]):
        course_rec = courses.create_course(
            db, name=model["course"]["fullname"] or model["course"]["code"],
            code=model["course"]["code"])
        courses.set_active(db, course_rec["id"])

    exported = None
    code = model["course"]["code"]
    if req.export == "notebooklm":
        exported = core.export_notebooklm(OUTPUT_DIR, combined=True, course=code)
    elif req.export == "all":
        exported = core.export_all_sources(OUTPUT_DIR, combined=True, course=code)

    _audit("moodle.api_import", target=code or host,
           detail=f"docs={downloaded['downloaded']} lectures={model['counts']['lectures']}",
           feature="moodle_import_url")

    return {
        "course": {**model["course"], "local_course": course_rec},
        "counts": model["counts"],
        "panopto_feeds": model["panopto_feeds"] if req.grab_lectures else [],
        "lectures": model["lectures"] if req.grab_lectures else [],
        "documents": model["documents"],
        "links": model["links"],
        "activities": model["activities"],
        "resources": downloaded,
        "converted": converted,
        "exported": exported,
        "outline": outline_rel,
        "keep_images": req.keep_images,
    }


@app.post("/api/moodle/quick-upload")
async def api_moodle_quick_upload(
    files: List[UploadFile] = File(...),
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
    sources.save_outline(OUTPUT_DIR, merged)

    downloaded = {"downloaded": 0, "files": [], "errors": []}
    converted = None
    if cookies.strip() and activities:
        res_dir = OUTPUT_DIR / "_resources"
        try:
            downloaded = moodle_resources.download_resources(
                activities, res_dir, cookies=cookies)
            if convert and downloaded["downloaded"]:
                converted = core.convert_documents(res_dir, OUTPUT_DIR, target="ai",
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


@app.post("/api/import/preflight")
def api_import_preflight(req: PreflightReq) -> Dict[str, Any]:
    """Validate a folder import before running it: counts, expected output, and
    dependency/size warnings (§7). Pure inspection - writes nothing."""
    try:
        return import_preflight.preflight_folder(Path(req.path).expanduser())
    except (NotADirectoryError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/import/folder")
def api_import_folder(req: FolderImportReq) -> Dict[str, Any]:
    """Recursively import a folder of mixed course material into the index (§7).
    Documents/subtitles are indexed; media files are listed for a later
    transcription job rather than processed inline."""
    cid = req.course_id if req.course_id is not None else settings_store.get_active_course(db)
    try:
        return folder_import.import_folder(db, OUTPUT_DIR, Path(req.path).expanduser(),
                                          course_id=cid,
                                          include_subfolders=req.include_subfolders)
    except (NotADirectoryError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/notion/convert")
def api_notion_convert(req: NotionRequest) -> Dict[str, Any]:
    """Convert a Notion HTML export (page, folder, or .zip) into clean Markdown."""
    try:
        result = notion.convert_notion_export(
            Path(req.path).expanduser(), OUTPUT_DIR, combined=req.combined
        )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Path not found: {req.path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/notion/upload")
async def api_notion_upload(file: UploadFile = File(...), combined: bool = False) -> Dict[str, Any]:
    """Convert an uploaded Notion export (.zip or a single .html) into Markdown."""
    suffix = Path(file.filename or "export.zip").suffix or ".zip"
    raw = await file.read()
    tmp = Path(tempfile.mkdtemp(prefix="notion_up_")) / f"upload{suffix}"
    tmp.write_bytes(raw)
    try:
        result = notion.convert_notion_export(tmp, OUTPUT_DIR, combined=combined)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read export: {e}")
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return result


@app.post("/api/docs/convert")
def api_docs_convert(req: DocsRequest) -> Dict[str, Any]:
    """Convert documents (pdf/pptx/docx/xlsx/html/…) to Markdown for AI ingestion."""
    try:
        result = core.convert_documents(
            Path(req.input_path).expanduser(),
            OUTPUT_DIR,
            exts=req.exts,
            include_subfolders=req.include_subfolders,
            overwrite=req.overwrite,
            target=req.target,
            combined=req.combined,
            keep_images=req.keep_images,
        )
    except RuntimeError as e:        # markitdown missing
        raise HTTPException(status_code=503, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Path not found: {req.input_path}")
    except (ValueError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get("/api/materials")
def api_materials(path: str) -> Dict[str, Any]:
    """Shallow listing of a local folder (slides, source code, etc.)."""
    root = Path(path).expanduser()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    entries: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        try:
            size = child.stat().st_size if child.is_file() else 0
        except Exception:
            size = 0
        entries.append(
            {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": size,
                "size_human": core.human_size(size) if child.is_file() else "",
                "path": str(child),
            }
        )
    return {"path": str(root), "entries": entries}


# ---------------------------------------------------------------------------
# Frontend (static files). Mounted last so /api/* wins.
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    # no-cache so a freshly updated index.html (and the assets it references) is
    # always picked up instead of a stale browser-cached copy.
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/docs", include_in_schema=False)
def docs() -> HTMLResponse:
    """Self-contained API docs (no CDN), rendered from /openapi.json.

    Works fully offline, unlike the default Swagger UI which fetches its
    JavaScript and CSS from a public CDN.
    """
    return HTMLResponse(_DOCS_HTML)


_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Course Assistant - API</title>
<style>
  :root { color-scheme: light dark; --bg:#f4f7fc; --surface:#fff; --ink:#1b2230;
    --muted:#5b6577; --border:#e3e9f3; --brand:#3b6ef5; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0e131c; --surface:#161d29;
    --ink:#e7ecf5; --muted:#9aa6bb; --border:#27313f; --brand:#5b8bff; } }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.55 system-ui,"Segoe UI",Roboto,Arial,sans-serif;
    color:var(--ink); background:var(--bg); padding:28px 22px; }
  .wrap { max-width:920px; margin:0 auto; }
  h1 { font-size:24px; margin:0 0 2px; }
  a.back { color:var(--brand); text-decoration:none; font-size:14px; }
  .sub { color:var(--muted); margin:4px 0 22px; }
  .ep { background:var(--surface); border:1px solid var(--border); border-radius:10px;
    padding:12px 14px; margin:10px 0; box-shadow:0 1px 3px rgba(20,30,60,.06); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .m { font-weight:700; font-size:12px; padding:3px 9px; border-radius:6px; color:#fff;
    letter-spacing:.04em; }
  .m.get{background:#1f9d63;} .m.post{background:#3b6ef5;} .m.put{background:#c9820a;}
  .m.delete{background:#d4433b;} .m.patch{background:#7c3aed;}
  .path { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:14px; }
  .summary { color:var(--muted); font-size:13.5px; margin-left:auto; }
  .body { margin:8px 0 0; padding-left:2px; font-size:13px; color:var(--muted); }
  code { background:rgba(127,127,127,.14); border-radius:5px; padding:1px 5px;
    font-size:12.5px; }
  .params { margin:6px 0 0; font-size:13px; }
  .params li { margin:2px 0; }
  .err { color:#d4433b; }
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/">← back to the app</a>
  <h1 id="title">API</h1>
  <p class="sub" id="sub">Loading the OpenAPI schema…</p>
  <div id="eps"></div>
</div>
<script>
const ORDER = { get:0, post:1, put:2, patch:3, delete:4 };
function esc(s){ return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function refName(schema){
  if(!schema) return null;
  if(schema.$ref) return schema.$ref.split('/').pop();
  if(schema.items && schema.items.$ref) return schema.items.$ref.split('/').pop()+'[]';
  return null;
}
(async () => {
  try {
    const spec = await (await fetch('/openapi.json')).json();
    document.getElementById('title').textContent =
      (spec.info && spec.info.title || 'API') + ' - v' + (spec.info && spec.info.version || '');
    const rows = [];
    for (const [path, methods] of Object.entries(spec.paths)) {
      for (const [method, op] of Object.entries(methods)) {
        rows.push({ path, method, op });
      }
    }
    rows.sort((a,b) => a.path.localeCompare(b.path) || (ORDER[a.method]-ORDER[b.method]));
    document.getElementById('sub').textContent =
      rows.length + ' endpoint' + (rows.length===1?'':'s') + ' - this page is generated locally, no internet required.';
    const host = document.getElementById('eps');
    for (const { path, method, op } of rows) {
      const div = document.createElement('div');
      div.className = 'ep';
      let html = '<div class="row"><span class="m '+method+'">'+method.toUpperCase()+'</span>'+
        '<span class="path">'+esc(path)+'</span>'+
        (op.summary ? '<span class="summary">'+esc(op.summary)+'</span>' : '')+'</div>';
      const params = (op.parameters||[]).map(p =>
        '<li><code>'+esc(p.name)+'</code> <span style="opacity:.7">('+esc(p.in)+
        (p.required?', required':'')+')</span></li>').join('');
      if (params) html += '<ul class="params">'+params+'</ul>';
      const rb = op.requestBody && op.requestBody.content && op.requestBody.content['application/json'];
      const bodyRef = rb && refName(rb.schema);
      if (bodyRef) html += '<div class="body">body: <code>'+esc(bodyRef)+'</code> (JSON)</div>';
      div.innerHTML = html;
      host.appendChild(div);
    }
  } catch (e) {
    document.getElementById('sub').className = 'sub err';
    document.getElementById('sub').textContent = 'Could not load /openapi.json: ' + e.message;
  }
})();
</script>
</body>
</html>"""


app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")