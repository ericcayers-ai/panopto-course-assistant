"""routers/library.py - library endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

import json

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from .. import core
from .. import search
from .. import settings_store
from .. import context
from ..context import _json_loads
from ..schemas import SavedViewCreate

router = APIRouter()


@router.get("/api/transcripts")
def api_transcripts() -> Dict[str, Any]:
    return {"output_dir": str(context.OUTPUT_DIR), "items": core.list_transcripts(context.OUTPUT_DIR)}


@router.get("/api/library")
def api_library() -> Dict[str, Any]:
    """Everything in the library, categorised (transcripts, documents, Notion,
    generated exports, and any other source files)."""
    return core.list_library(context.OUTPUT_DIR)


@router.get("/api/transcript")
def api_transcript(path: str) -> Dict[str, Any]:
    try:
        content = core.read_transcript_file(context.OUTPUT_DIR, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Transcript not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"path": path, "content": content}


@router.get("/api/search")
def api_search(q: str, week: Optional[int] = None, type: str = "",
               fuzzy: bool = True) -> Dict[str, Any]:
    """Full-text search with optional metadata filters + a fuzzy title fallback (§2)."""
    return {"query": q, "results": search.search(context.OUTPUT_DIR, q, week=week, ftype=type,
                                                 fuzzy=fuzzy)}


@router.get("/api/index")
def api_index(week: Optional[int] = None, type: str = "", tag: str = "",
              q: str = "", sort: str = "date") -> Dict[str, Any]:
    """Unified, filterable/sortable library index (§2): sort by date/name/week,
    filter by week/type/tag, tag-aware search."""
    return search.library_view(context.OUTPUT_DIR, week=week, ftype=type, tag=tag, q=q, sort=sort)


@router.get("/api/related")
def api_related(path: str) -> Dict[str, Any]:
    return {"path": path, "related": search.related(context.OUTPUT_DIR, path)}


@router.get("/api/views")
def api_views_list() -> Dict[str, Any]:
    active = settings_store.get_active_course(context.db)
    saved = [
        {"id": v["id"], "name": v["name"], "builtin": False,
         "query": _json_loads(v["query_json"])}
        for v in context.db.list_saved_views(active)
    ]
    return {"views": search.BUILTIN_VIEWS + saved}


@router.post("/api/views")
def api_views_create(req: SavedViewCreate) -> Dict[str, Any]:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="View name is required.")
    vid = context.db.create_saved_view(req.name.strip(), json.dumps(req.query or {}),
                              course_id=settings_store.get_active_course(context.db))
    return {"id": vid, "name": req.name.strip(), "builtin": False, "query": req.query or {}}


@router.delete("/api/views/{view_id}")
def api_views_delete(view_id: int) -> Dict[str, Any]:
    if not context.db.delete_saved_view(view_id):
        raise HTTPException(status_code=404, detail="Saved view not found")
    return {"deleted": view_id}


@router.get("/api/materials")
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
