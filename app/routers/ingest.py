"""routers/ingest.py - ingest endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

import shutil
import tempfile

from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from .. import core
from .. import notion
from .. import settings_store
from .. import transcribe
from ..imports import folder as folder_import
from ..imports import preflight as import_preflight
from .. import context
from ..schemas import DocsRequest, FeedRequest, FolderImportReq, NotionRequest, PanoptoDownloadRequest, PdfRequest, PreflightReq

router = APIRouter()


@router.post("/api/feed")
def api_feed(req: FeedRequest) -> Dict[str, Any]:
    try:
        items = core.parse_feed(req.source, cookies=req.cookies)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse feed: {e}")
    return {"count": len(items), "lectures": [it.to_dict() for it in items]}


@router.post("/api/panopto/download")
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


@router.post("/api/feed/upload")
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


@router.post("/api/pdf/convert")
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


@router.post("/api/import/preflight")
def api_import_preflight(req: PreflightReq) -> Dict[str, Any]:
    """Validate a folder import before running it: counts, expected output, and
    dependency/size warnings (§7). Pure inspection - writes nothing."""
    try:
        return import_preflight.preflight_folder(Path(req.path).expanduser())
    except (NotADirectoryError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/import/folder")
def api_import_folder(req: FolderImportReq) -> Dict[str, Any]:
    """Recursively import a folder of mixed course material into the index (§7).
    Documents/subtitles are indexed; media files are listed for a later
    transcription job rather than processed inline."""
    cid = req.course_id if req.course_id is not None else settings_store.get_active_course(context.db)
    try:
        return folder_import.import_folder(context.db, context.OUTPUT_DIR, Path(req.path).expanduser(),
                                          course_id=cid,
                                          include_subfolders=req.include_subfolders)
    except (NotADirectoryError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/notion/convert")
def api_notion_convert(req: NotionRequest) -> Dict[str, Any]:
    """Convert a Notion HTML export (page, folder, or .zip) into clean Markdown."""
    try:
        result = notion.convert_notion_export(
            Path(req.path).expanduser(), context.OUTPUT_DIR, combined=req.combined
        )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Path not found: {req.path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/api/notion/upload")
async def api_notion_upload(file: UploadFile = File(...), combined: bool = False) -> Dict[str, Any]:
    """Convert an uploaded Notion export (.zip or a single .html) into Markdown."""
    suffix = Path(file.filename or "export.zip").suffix or ".zip"
    raw = await file.read()
    tmp = Path(tempfile.mkdtemp(prefix="notion_up_")) / f"upload{suffix}"
    tmp.write_bytes(raw)
    try:
        result = notion.convert_notion_export(tmp, context.OUTPUT_DIR, combined=combined)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read export: {e}")
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return result


@router.post("/api/docs/convert")
def api_docs_convert(req: DocsRequest) -> Dict[str, Any]:
    """Convert documents (pdf/pptx/docx/xlsx/html/…) to Markdown for AI ingestion."""
    try:
        result = core.convert_documents(
            Path(req.input_path).expanduser(),
            context.OUTPUT_DIR,
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
