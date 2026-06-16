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
POST /api/flashcards/generate -> Anki-importable flashcards from transcripts
POST /api/flashcards/categorize -> tag/categorise an existing flashcard deck
POST /api/export/notion-csv -> export a Notion-importable study-database CSV
POST /api/transcribe       -> queue a transcription job (needs whisper installed)
POST /api/organize         -> reorganize existing transcripts into folders
POST /api/moodle/parse     -> parse a Moodle course HTML export into an outline
POST /api/notion/convert   -> convert a Notion HTML export into Markdown
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

from . import core, transcribe, sources, notion, flashcards, study
from .jobs import manager

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
APP_VERSION = "1.1.0"
# Where transcripts are written/read. Override with PANOPTO_OUTPUT.
OUTPUT_DIR = Path(os.environ.get("PANOPTO_OUTPUT", BASE_DIR / "transcripts")).resolve()
core.ensure_dir(OUTPUT_DIR)

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
