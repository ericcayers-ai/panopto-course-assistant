"""routers/ollama.py - ollama endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from typing import Any
from typing import Dict
from .. import llm
from .. import ollama_mgr
from .. import settings_store
from ..jobs import manager
from .. import context
from ..context import _safe_ai_config
from ..schemas import OllamaInitRequest, OllamaPullRequest, OllamaUseRequest

router = APIRouter()


@router.get("/api/ollama/status")
def api_ollama_status() -> Dict[str, Any]:
    return ollama_mgr.status()


@router.post("/api/ollama/start")
def api_ollama_start() -> Dict[str, Any]:
    try:
        return ollama_mgr.start_server()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/ollama/pull")
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


@router.post("/api/ollama/use")
def api_ollama_use(req: OllamaUseRequest) -> Dict[str, Any]:
    """Point the app's AI features at the local Ollama with the chosen model."""
    model = (req.model or ollama_mgr.DEFAULT_MODEL).strip()
    cid = settings_store.get_active_course(context.db)
    cfg = llm.set_config(context.db, cid, {"provider": "ollama", "model": model,
                                   "host": ollama_mgr.DEFAULT_HOST})
    return {"ok": True, "config": _safe_ai_config(cfg)}


@router.post("/api/ollama/install")
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


@router.post("/api/ollama/initialize")
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
    cid = settings_store.get_active_course(context.db)
    llm.set_config(context.db, cid, {"provider": "ollama", "model": model,
                              "host": ollama_mgr.DEFAULT_HOST})
    return s
