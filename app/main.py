"""
main.py — FastAPI backend for the Panopto Course Assistant.

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

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, transcribe, sources, notion, flashcards, study, database, courses, settings_store, search, llm, ai
from .jobs import manager

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
APP_VERSION = "1.3.0"
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
    apply *heuristic caching* — happily serving a stale app.js/style.css after an
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


class NotebookLMRequest(BaseModel):
    selection: Optional[List[str]] = None  # ["folder/stem", ...]; None = all
    combined: bool = False                 # also write a single course_pack.md
    course: str = ""                       # optional course name for headers


class ExportAllRequest(BaseModel):
    combined: bool = True                  # write a single everything_pack.md
    course: str = ""                       # optional course name for headers


class FormatsRequest(BaseModel):
    formats: List[str] = ["srt"]           # srt | vtt | txt | md | notebooklm | summary
    interval: int = 30


class FlashcardGenRequest(BaseModel):
    selection: Optional[List[str]] = None  # limit to these lecture stems; None = all
    course: str = ""
    deck: str = "flashcards"
    prefer: str = "summary"                # "summary" | "text"
    max_per_lecture: int = 15


class FlashcardCatRequest(BaseModel):
    text: str = ""                         # pasted CSV/TSV deck (front, back[, tags])
    path: str = ""                         # …or a path to a .csv/.tsv/.txt deck
    course: str = ""
    extra_keywords: Optional[List[str]] = None
    deck: str = "categorized"


class StudyCsvRequest(BaseModel):
    course: str = ""
    filename: str = "study_database"


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
    status["ai"]["config"] = _safe_ai_config(llm.get_config(db, settings_store.get_active_course(db)))
    return status


def _safe_ai_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Never echo a stored API key back to the client — report only its presence."""
    out = {k: v for k, v in cfg.items() if k != "api_key"}
    out["has_api_key"] = bool(cfg.get("api_key"))
    return out


# ---------------------------------------------------------------------------
# Courses (§1 — multi-course foundation)
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
    if not db.get_course(course_id):
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
    # Course-archive export is owned by the Export Engine (roadmap §9); this
    # endpoint is the stable entry point that §9 will fill in.
    if not db.get_course(course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    raise HTTPException(status_code=501, detail="Course archive export arrives with §9 (Export Engine).")


# ---------------------------------------------------------------------------
# Settings (§1 — persistent preferences)
# ---------------------------------------------------------------------------


@app.get("/api/settings")
def api_settings_get() -> Dict[str, Any]:
    return settings_store.all(db)


@app.put("/api/settings")
def api_settings_update(req: SettingsUpdate) -> Dict[str, Any]:
    return settings_store.update(db, req.values)


# ---------------------------------------------------------------------------
# Optional AI / LLM (§4) — every endpoint degrades to an extractive fallback
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
    return ai.chat(OUTPUT_DIR, req.query, history=req.history, db=db,
                  course_id=settings_store.get_active_course(db))


@app.post("/api/feed")
def api_feed(req: FeedRequest) -> Dict[str, Any]:
    try:
        items = core.parse_feed(req.source, cookies=req.cookies)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse feed: {e}")
    return {"count": len(items), "lectures": [it.to_dict() for it in items]}


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
    import json as _json
    vid = db.create_saved_view(req.name.strip(), _json.dumps(req.query or {}),
                              course_id=settings_store.get_active_course(db))
    return {"id": vid, "name": req.name.strip(), "builtin": False, "query": req.query or {}}


@app.delete("/api/views/{view_id}")
def api_views_delete(view_id: int) -> Dict[str, Any]:
    if not db.delete_saved_view(view_id):
        raise HTTPException(status_code=404, detail="Saved view not found")
    return {"deleted": view_id}


def _json_loads(raw: str) -> Dict[str, Any]:
    import json as _json
    try:
        return _json.loads(raw) if raw else {}
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


@app.post("/api/export/notion-csv")
def api_export_notion_csv(req: StudyCsvRequest) -> Dict[str, Any]:
    """Export the transcript library as a Notion-importable study-database CSV."""
    result = study.write_study_database(OUTPUT_DIR, course=req.course, filename=req.filename)
    if result["count"] == 0:
        raise HTTPException(
            status_code=404,
            detail="Nothing to export yet. Transcribe or convert some lectures first.",
        )
    return result


@app.post("/api/flashcards/generate")
def api_flashcards_generate(req: FlashcardGenRequest) -> Dict[str, Any]:
    """Generate Anki-importable flashcards (auto-tagged) from existing transcripts."""
    cards = flashcards.generate_from_library(
        OUTPUT_DIR,
        selection=req.selection,
        course=req.course,
        prefer=req.prefer,
        max_per_lecture=max(1, min(req.max_per_lecture, 50)),
    )
    if not cards:
        raise HTTPException(
            status_code=404,
            detail="No flashcards could be generated. Transcribe or convert some "
            "lectures first (summaries give the best cards).",
        )
    result = flashcards.write_deck(OUTPUT_DIR, cards, req.deck)
    result["course"] = req.course
    return result


@app.post("/api/flashcards/categorize")
def api_flashcards_categorize(req: FlashcardCatRequest) -> Dict[str, Any]:
    """Add study tags to an existing deck (pasted text or a file path)."""
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
    vocab = flashcards.build_vocabulary(OUTPUT_DIR, req.course, req.extra_keywords)
    cards = flashcards.categorise_cards(cards, vocab, req.course)
    result = flashcards.write_deck(OUTPUT_DIR, cards, req.deck)
    result["course"] = req.course
    return result


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
        raise HTTPException(status_code=409, detail="Job already finished — nothing to cancel.")
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
    import tempfile

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
        import shutil
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
<title>Course Assistant — API</title>
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
      (spec.info && spec.info.title || 'API') + ' — v' + (spec.info && spec.info.version || '');
    const rows = [];
    for (const [path, methods] of Object.entries(spec.paths)) {
      for (const [method, op] of Object.entries(methods)) {
        rows.push({ path, method, op });
      }
    }
    rows.sort((a,b) => a.path.localeCompare(b.path) || (ORDER[a.method]-ORDER[b.method]));
    document.getElementById('sub').textContent =
      rows.length + ' endpoint' + (rows.length===1?'':'s') + ' — this page is generated locally, no internet required.';
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
