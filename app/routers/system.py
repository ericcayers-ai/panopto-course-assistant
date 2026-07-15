"""routers/system.py - system endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
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


def _output_writable(output_dir: Path) -> bool:
    """True when we can create + write under the library directory."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _have_mod(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


@router.get("/api/health")
def api_health() -> Dict[str, Any]:
    """Lightweight liveness check (version + whether the library dir is writable)."""
    return {
        "ok": True,
        "version": context.APP_VERSION,
        "output_writable": _output_writable(context.OUTPUT_DIR),
    }


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


@router.get("/api/setup/preflight")
def api_setup_preflight() -> Dict[str, Any]:
    """First-run / troubleshooting snapshot with actionable remediations.

    Reuses :func:`backup.environment_report` and STT
    :func:`model_mgmt.preflight_install` plus live engine availability.
    """
    env = backup_mod.environment_report(context.OUTPUT_DIR)
    writable = _output_writable(context.OUTPUT_DIR)

    stt_preflight: Dict[str, Any] = {}
    engines: Dict[str, bool] = {}
    try:
        from ..stt import models as model_mgmt
        from ..stt.engines import availability_map
        stt_preflight = model_mgmt.preflight_install()
        engines = availability_map()
    except Exception as e:
        stt_preflight = {"ok": False, "error": str(e)[:200]}
        engines = {}

    packs = {
        "base": bool(engines.get("faster-whisper") or env.get("any_engine")),
        "quality": bool(engines.get("granite") or engines.get("qwen3")),
        "speakers": _have_mod("pyannote"),
        "live": bool(engines.get("moonshine")),
        "specialist": bool(engines.get("firered") or engines.get("omnilingual")),
    }

    remediations: List[Dict[str, str]] = []
    if not writable:
        remediations.append({
            "id": "output_writable",
            "severity": "error",
            "message": "Library folder is not writable.",
            "fix": f"Choose a writable output directory (currently {context.OUTPUT_DIR}).",
        })
    if not env.get("any_engine"):
        remediations.append({
            "id": "stt_base",
            "severity": "error",
            "message": "No transcription engine is installed.",
            "fix": "pip install -r requirements-stt-base.txt",
        })
    if not packs["quality"]:
        remediations.append({
            "id": "stt_quality",
            "severity": "info",
            "message": "Granite/Qwen quality engines are unavailable.",
            "fix": "pip install -r requirements-stt-quality.txt",
        })
    if not packs["speakers"]:
        remediations.append({
            "id": "stt_speakers",
            "severity": "info",
            "message": "Speaker diarization (pyannote) is not installed.",
            "fix": "pip install -r requirements-stt-speakers.txt "
                   "(accept HF license; store HF token in secrets once).",
        })
    if not packs["live"]:
        remediations.append({
            "id": "stt_live",
            "severity": "info",
            "message": "Moonshine live/edge engine is unavailable.",
            "fix": "pip install -r requirements-stt-live.txt "
                   "(then useful-moonshine / moonshine).",
        })
    if not stt_preflight.get("ffmpeg", True):
        remediations.append({
            "id": "ffmpeg",
            "severity": "warn",
            "message": "ffmpeg was not detected on PATH.",
            "fix": "Install ffmpeg and ensure it is available in PATH.",
        })
    for warning in stt_preflight.get("warnings") or []:
        remediations.append({
            "id": "disk",
            "severity": "warn",
            "message": str(warning),
            "fix": "Free disk space or point PANOPTO_OUTPUT at a larger drive.",
        })
    secrets = secret_store.backend_status()
    if not secrets.get("encrypted"):
        remediations.append({
            "id": "secrets",
            "severity": "warn",
            "message": secrets.get("warning") or "Secrets store is not encrypted.",
            "fix": "pip install keyring  (or cryptography for encrypted-file fallback).",
        })

    ok = writable and bool(env.get("any_engine")) and stt_preflight.get("ok", True)
    return {
        "ok": ok,
        "version": context.APP_VERSION,
        "output_writable": writable,
        "environment": env,
        "stt_preflight": stt_preflight,
        "engines": engines,
        "packs": packs,
        "secrets_backend": secrets.get("backend"),
        "remediations": remediations,
    }


@router.post("/api/diagnostics/bundle")
def api_diagnostics_bundle() -> Dict[str, Any]:
    """Plain-text diagnostics for a user 'Copy diagnostics' action.

    Never includes secret *values* — only backend names and presence flags.
    """
    env = backup_mod.environment_report(context.OUTPUT_DIR)
    health = api_health()
    secrets = secret_store.backend_status()
    ai = llm.detect()
    ai_ready = sorted(
        name for name, meta in ((ai or {}).get("providers") or {}).items()
        if (meta or {}).get("ready")
    )

    engines: Dict[str, bool] = {}
    stt_preflight: Dict[str, Any] = {}
    cache: Dict[str, Any] = {}
    try:
        from ..stt import models as model_mgmt
        from ..stt.engines import availability_map
        engines = availability_map()
        stt_preflight = model_mgmt.preflight_install()
        cache = {
            "dir_set": True,
            "bytes": model_mgmt.cache_size_bytes(),
            "models": len(model_mgmt.list_cached_models() or []),
        }
    except Exception as e:
        stt_preflight = {"error": str(e)[:200]}
        cache = {"dir_set": False}

    lines = [
        f"Course Assistant diagnostics",
        f"version: {context.APP_VERSION}",
        f"python: {env.get('python')}",
        f"platform: {env.get('platform')}",
        f"machine: {env.get('machine')}",
        f"output_writable: {health.get('output_writable')}",
        f"free_disk_gb: {env.get('free_disk_gb')}",
        f"any_engine: {env.get('any_engine')}",
        f"cuda: {env.get('cuda')}",
        f"secrets_backend: {secrets.get('backend')}",
        f"secrets_encrypted: {secrets.get('encrypted')}",
        f"ai_providers_ready: {','.join(ai_ready) or 'none'}",
        f"ffmpeg: {stt_preflight.get('ffmpeg')}",
        f"vram_mb: {stt_preflight.get('vram_mb')}",
        f"ram_mb: {stt_preflight.get('ram_mb')}",
        f"stt_cache_models: {cache.get('models')}",
        f"stt_cache_bytes: {cache.get('bytes')}",
        "engines:",
    ]
    if engines:
        for name in sorted(engines):
            lines.append(f"  {name}: {engines[name]}")
    else:
        legacy = env.get("engines") or {}
        for name in sorted(legacy):
            lines.append(f"  {name}: {legacy[name]}")
    lines.append("optional:")
    for name, present in sorted((env.get("optional") or {}).items()):
        lines.append(f"  {name}: {present}")
    if env.get("missing"):
        lines.append("missing: " + ", ".join(env["missing"]))

    text = "\n".join(lines) + "\n"
    return {"text": text, "ok": True}


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
