"""routers/tts.py - tts endpoints (§17: split out of main.py)."""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Any
from typing import Dict
from .. import settings_store
from .. import tts as tts_mod
from ..jobs import manager
from .. import context
from ..schemas import TtsGenerateRequest

router = APIRouter()


@router.get("/api/tts/status")
def api_tts_status() -> Dict[str, Any]:
    """Check whether Kokoro is installed and return the voice catalog."""
    available = tts_mod.is_available()
    voices = tts_mod.list_voices(context.OUTPUT_DIR / "_tts_voices")
    return {
        "available": available,
        "voices": voices,
        "engine": "kokoro",
        "model": tts_mod.MODEL_ID,
    }


@router.post("/api/tts/generate")
def api_tts_generate(req: TtsGenerateRequest) -> Dict[str, Any]:
    """Queue a Kokoro TTS job. The audio is saved inside the _tts/ subdirectory."""
    if not tts_mod.is_available():
        raise HTTPException(
            status_code=503,
            detail="Kokoro TTS is not installed. Run: pip install -r requirements-tts.txt",
        )
    md_path = req.md_path.strip()
    if not md_path or not Path(md_path).is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {md_path!r}")
    if not md_path.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are supported.")

    stem = Path(md_path).stem
    safe_stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)[:60]
    safe_voice = "".join(c if c.isalnum() or c in "-_." else "_" for c in req.voice)
    out_path = str(context.OUTPUT_DIR / "_tts" / f"{safe_stem}_{safe_voice}.wav")

    captured = {
        "md_path": md_path,
        "voice": req.voice,
        "output_path": out_path,
        "model_path": req.model_path or tts_mod.MODEL_ID,
        "speed": float(req.speed or 1.0),
    }

    voices_cache = context.OUTPUT_DIR / "_tts_voices"

    def work(_progress):
        result = tts_mod.generate(
            md_path=captured["md_path"],
            voice_id=captured["voice"],
            output_path=captured["output_path"],
            cache_dir=voices_cache,
            progress=_progress,
            model_path=captured["model_path"],
            speed=captured["speed"],
        )
        return result

    job = manager.submit(
        f"TTS: {Path(md_path).name} ({req.voice})",
        work,
        type="tts",
        payload=captured,
        course_id=settings_store.get_active_course(context.db),
    )
    return {**job.to_dict(), "output_path": out_path}


@router.get("/api/tts/audio")
def api_tts_audio(path: str) -> FileResponse:
    """Stream a generated WAV file. Only serves files inside OUTPUT_DIR/_tts/."""
    tts_dir = (context.OUTPUT_DIR / "_tts").resolve()
    target = Path(path).resolve()
    if not str(target).startswith(str(tts_dir)):
        raise HTTPException(status_code=403, detail="Access denied.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return FileResponse(str(target), media_type="audio/wav",
                        filename=target.name)
