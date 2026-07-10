"""routers/system.py - system endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from typing import Dict
from .. import backup as backup_mod
from .. import core
from .. import imageextract
from .. import llm
from .. import nativeui
from .. import secrets as secret_store
from .. import settings_store
from .. import transcribe
from .. import context
from ..context import _safe_ai_config
from ..schemas import PickFileRequest, PickFolderRequest, PickSaveRequest, RestoreReq, SettingsUpdate

router = APIRouter()


@router.get("/api/status")
def api_status() -> Dict[str, Any]:
    status = transcribe.engine_status()
    status["output_dir"] = str(context.OUTPUT_DIR)
    status["output_choices"] = core.OUTPUT_CHOICES
    status["organize_choices"] = core.ORG_CHOICES
    status["doc_exts"] = core.DOC_EXTS
    status["db"] = {
        "schema_version": context.db.schema_version(),
        "courses": context.db.count_courses(),
        "active_course": settings_store.get_active_course(context.db),
    }
    status["ai"] = llm.detect()
    _cfg = llm.get_config(context.db, settings_store.get_active_course(context.db))
    status["ai"]["config"] = _safe_ai_config(_cfg)
    status["llm_ready"] = llm.is_enabled(_cfg)
    status["secrets"] = secret_store.backend_status()
    status["privacy"] = secret_store.transparency()
    status["transcribe_recommended"] = transcribe.recommend_settings()
    status["image_extraction"] = imageextract.capability()
    return status


@router.get("/api/transcribe/recommend")
def api_transcribe_recommend() -> Dict[str, Any]:
    """Best transcription settings for this machine (Simple-mode auto-transcribe)."""
    return transcribe.recommend_settings()


@router.post("/api/pick-folder")
def api_pick_folder(req: PickFolderRequest) -> Dict[str, Any]:
    """Open a native folder picker and return the chosen path (null if cancelled).

    ``available`` is false when no desktop dialog can be shown (e.g. a headless
    host), so the frontend can fall back to a typed path."""
    if not nativeui.available():
        return {"path": None, "available": False}
    path = nativeui.pick_directory(req.title or "Choose a folder", str(context.OUTPUT_DIR))
    return {"path": path, "available": True}


@router.post("/api/pick-save")
def api_pick_save(req: PickSaveRequest) -> Dict[str, Any]:
    """Open a native 'Save As' dialog and return the chosen file path (null if
    cancelled)."""
    if not nativeui.available():
        return {"path": None, "available": False}
    path = nativeui.pick_save_file(req.title or "Save as", req.default_name,
                                   str(context.OUTPUT_DIR), req.ext or "")
    return {"path": path, "available": True}


@router.post("/api/pick-file")
def api_pick_file(req: PickFileRequest) -> Dict[str, Any]:
    """Open a native file-open dialog and return the chosen path (null if cancelled)."""
    if not nativeui.available():
        return {"path": None, "available": False}
    path = nativeui.pick_open_file(req.title or "Open file", str(context.OUTPUT_DIR), req.ext or "")
    return {"path": path, "available": True}


@router.get("/api/settings")
def api_settings_get() -> Dict[str, Any]:
    return settings_store.all(context.db)


@router.put("/api/settings")
def api_settings_update(req: SettingsUpdate) -> Dict[str, Any]:
    return settings_store.update(context.db, req.values)


@router.get("/api/environment")
def api_environment() -> Dict[str, Any]:
    """What this machine can do (present/missing engines + deps + disk)."""
    return backup_mod.environment_report(context.OUTPUT_DIR)


@router.post("/api/backup")
def api_backup() -> Dict[str, Any]:
    """Zip the DB + whole library into one portable file (secrets excluded)."""
    return backup_mod.create_backup(context.OUTPUT_DIR)


@router.post("/api/restore")
def api_restore(req: RestoreReq) -> Dict[str, Any]:
    """Unpack a backup into the library (safe merge unless overwrite=true)."""
    try:
        result = backup_mod.restore_backup(Path(req.path).expanduser(), context.OUTPUT_DIR,
                                          overwrite=req.overwrite)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Backup not found: {e}")
    # The DB file is held open here; a full DB replace (overwrite=true) only takes
    # effect on the next launch, which migrates it forward automatically.
    result["restart_required_for_db"] = req.overwrite
    return result
