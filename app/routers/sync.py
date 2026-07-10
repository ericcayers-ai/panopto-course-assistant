"""routers/sync.py - sync endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from typing import Any
from typing import Dict
from .. import core
from ..integrations import anki as anki_sync
from ..integrations import notion as notion_sync
from ..integrations import state as sync_state
from .. import context
from ..context import _anki_cards, _audit, _notion_args
from ..schemas import AnkiSyncReq, MappingReq, NotionSyncReq

router = APIRouter()


@router.get("/api/sync/status")
def api_sync_status() -> Dict[str, Any]:
    return sync_state.public_status(context.db)


@router.put("/api/sync/mapping")
def api_sync_mapping(req: MappingReq) -> Dict[str, Any]:
    if req.target != "notion":
        raise HTTPException(status_code=400, detail="Only the Notion mapping is editable.")
    cfg = sync_state.set_target(context.db, "notion", {"field_map": req.fields})
    return {"target": "notion", "field_map": cfg["notion"]["field_map"]}


@router.post("/api/sync/notion/dryrun")
def api_sync_notion_dryrun(req: NotionSyncReq) -> Dict[str, Any]:
    try:
        return notion_sync.sync_course(context.OUTPUT_DIR, dry_run=True, **_notion_args(req))
    except notion_sync.NotionError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/api/sync/notion")
def api_sync_notion(req: NotionSyncReq) -> Dict[str, Any]:
    try:
        result = notion_sync.sync_course(context.OUTPUT_DIR, dry_run=False, **_notion_args(req))
    except notion_sync.NotionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    sync_state.set_target(context.db, "notion", {"last_sync": core.now_iso(),
                                        "database_id": _notion_args(req)["database_id"]})
    _audit("sync.notion", target="notion",
           detail=f"created={result.get('created',0)} updated={result.get('updated',0)}",
           feature="sync_notion")
    return result


@router.post("/api/sync/anki/dryrun")
def api_sync_anki_dryrun(req: AnkiSyncReq) -> Dict[str, Any]:
    url = req.url or sync_state.get(context.db)["anki"].get("url", "")
    return anki_sync.sync_flashcards(_anki_cards(req), req.deck, course=req.course,
                                    dry_run=True, url=url)


@router.post("/api/sync/anki")
def api_sync_anki(req: AnkiSyncReq) -> Dict[str, Any]:
    url = req.url or sync_state.get(context.db)["anki"].get("url", "")
    try:
        result = anki_sync.sync_flashcards(_anki_cards(req), req.deck, course=req.course,
                                          dry_run=False, url=url)
    except anki_sync.AnkiError as e:
        raise HTTPException(status_code=502, detail=str(e))
    sync_state.set_target(context.db, "anki", {"last_sync": core.now_iso(), "url": url})
    _audit("sync.anki", target=req.deck,
           detail=f"added={result.get('added',0)}", feature="sync_anki")
    return result
