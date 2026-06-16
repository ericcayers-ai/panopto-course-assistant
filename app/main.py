"""
main.py — FastAPI backend for the Panopto Course Assistant.

Endpoints
---------
GET  /                     -> serves the frontend (static/index.html)
GET  /api/status           -> which optional engines/deps are installed
POST /api/feed             -> parse an RSS feed (URL or local path) -> lectures
POST /api/feed/upload      -> parse an uploaded RSS .xml file -> lectures
GET  /api/transcripts      -> list transcripts in the output directory
GET  /api/transcript       -> read one transcript file (?path=)
GET  /api/search           -> full-text search across transcripts (?q=)
POST /api/export/notebooklm -> render transcripts into NotebookLM-friendly Markdown
POST /api/transcribe       -> queue a transcription job (needs whisper installed)
POST /api/organize         -> reorganize existing transcripts into folders
POST /api/moodle/parse     -> parse a Moodle course HTML export into an outline
POST /api/notion/convert   -> convert a Notion HTML export into Markdown
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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, transcribe, sources, notion
from .jobs import manager

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
# Where transcripts are written/read. Override with PANOPTO_OUTPUT.
OUTPUT_DIR = Path(os.environ.get("PANOPTO_OUTPUT", BASE_DIR / "transcripts")).resolve()
core.ensure_dir(OUTPUT_DIR)

app = FastAPI(title="Panopto Course Assistant", version="1.0.0")


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
    organize: str = "week"
    outputs: List[str] = ["txt", "srt", "md", "json"]
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


class NotebookLMRequest(BaseModel):
    selection: Optional[List[str]] = None  # ["folder/stem", ...]; None = all
    combined: bool = False                 # also write a single course_pack.md
    course: str = ""                       # optional course name for headers


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    status = transcribe.engine_status()
    status["output_dir"] = str(OUTPUT_DIR)
    status["output_choices"] = core.OUTPUT_CHOICES
    status["organize_choices"] = core.ORG_CHOICES
    return status


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
def api_search(q: str) -> Dict[str, Any]:
    return {"query": q, "results": core.search_transcripts(OUTPUT_DIR, q)}


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

    def work(progress) -> Dict[str, Any]:
        return transcribe.transcribe_lecture(
            item,
            OUTPUT_DIR,
            engine=req.engine,
            model=req.model,
            language=req.language,
            device=req.device,
            organize=req.organize,
            outputs=req.outputs,
            interval=req.interval,
            keep_media=req.keep_media,
            audio_only=req.audio_only,
            skip_existing=req.skip_existing,
            force=req.force,
            cookies=req.cookies,
            course=req.course,
            progress=progress,
        )

    job = manager.submit(item.title, work)
    return job.to_dict()


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
    """Convert a Notion HTML export (page or folder) into clean Markdown."""
    try:
        result = notion.convert_notion_export(
            Path(req.path).expanduser(), OUTPUT_DIR, combined=req.combined
        )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Path not found: {req.path}")
    except ValueError as e:
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
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
