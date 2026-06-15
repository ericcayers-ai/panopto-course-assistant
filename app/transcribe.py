"""
transcribe.py — optional download + transcription pipeline.

Heavy dependencies (torch, whisper, faster-whisper, yt-dlp) are imported lazily
so that the web app starts and serves the feed/search/PDF features even when the
transcription stack is not installed. Use ``engine_status()`` to report what is
available to the frontend.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import core
from .core import LectureItem, ensure_dir


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def engine_status() -> Dict[str, Any]:
    """Report which transcription engines / helpers are installed."""
    engines = {
        "faster-whisper": _have("faster_whisper"),
        "whisper": _have("whisper"),
    }
    return {
        "engines": engines,
        "any_engine": any(engines.values()),
        "yt_dlp": _have("yt_dlp"),
        "requests": _have("requests"),
        "markitdown": _have("markitdown"),
        "torch": _have("torch"),
        "cuda": _cuda_available(),
        "default_engine": "faster-whisper" if engines["faster-whisper"] else (
            "whisper" if engines["whisper"] else None
        ),
    }


def _cuda_available() -> bool:
    if not _have("torch"):
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def resolve_device(requested: str) -> str:
    requested = (requested or "auto").lower()
    if requested in {"cpu", "cuda"}:
        return requested
    return "cuda" if _cuda_available() else "cpu"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_media(
    url: str,
    dest: Path,
    cookies: str = "",
    progress: Optional[Callable[[float], None]] = None,
) -> Path:
    """Direct HTTP download with a yt-dlp fallback."""
    import requests

    ensure_dir(dest.parent)
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            with dest.open("wb") as f:
                for chunk in r.iter_content(1 << 18):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(0.99, done / total))
        return dest
    except Exception:
        if dest.exists():
            try:
                dest.unlink()
            except Exception:
                pass

    # Fallback: yt-dlp (handles auth / redirects).
    try:
        import yt_dlp
    except Exception as e:
        raise RuntimeError(
            "Direct download failed and yt-dlp is not installed (pip install yt-dlp)"
        ) from e

    opts: Dict[str, Any] = {"outtmpl": str(dest), "quiet": True, "no_warnings": True}
    if cookies:
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return dest


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


def _transcribe_faster_whisper(media: Path, model_name: str, language: str, device: str,
                               beam_size: int, vad_filter: bool) -> Dict[str, Any]:
    from faster_whisper import WhisperModel

    compute_type = "float16" if device == "cuda" else "int8"
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments_iter, info = model.transcribe(
        str(media),
        language=language or None,
        beam_size=beam_size,
        vad_filter=vad_filter,
        condition_on_previous_text=True,
    )
    segments, parts = [], []
    for seg in segments_iter:
        t = seg.text.strip()
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": t})
        parts.append(t)
    return {
        "segments": segments,
        "text": " ".join(p for p in parts if p).strip(),
        "language": getattr(info, "language", None) or language or "",
    }


def _transcribe_openai_whisper(media: Path, model_name: str, language: str, device: str,
                               beam_size: int) -> Dict[str, Any]:
    import whisper

    if model_name == "large":
        model_name = "large-v3"
    model = whisper.load_model(model_name, device=device)
    result = model.transcribe(
        str(media),
        language=language or None,
        verbose=False,
        fp16=(device == "cuda"),
        beam_size=beam_size,
        condition_on_previous_text=True,
    )
    segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": (s["text"] or "").strip()}
        for s in result.get("segments", [])
    ]
    return {
        "segments": segments,
        "text": (result.get("text") or "").strip(),
        "language": result.get("language") or language or "",
    }


# ---------------------------------------------------------------------------
# Top-level pipeline for one lecture
# ---------------------------------------------------------------------------


def transcribe_lecture(
    item: LectureItem,
    output_root: Path,
    *,
    engine: str = "faster-whisper",
    model: str = "small",
    language: str = "en",
    device: str = "auto",
    organize: str = "week",
    outputs: List[str] = None,
    interval: int = 30,
    beam_size: int = 5,
    vad_filter: bool = True,
    keep_media: bool = False,
    cookies: str = "",
    progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    outputs = outputs or ["txt", "srt", "md", "json"]
    device = resolve_device(device)

    def report(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    out_dir = core.output_dir_for(output_root, item, organize)
    ensure_dir(out_dir)
    media = out_dir / f"{item.safe_title}.mp4"

    report("downloading", 0.0)
    download_media(item.url, media, cookies=cookies, progress=lambda f: report("downloading", f * 0.4))

    report("transcribing", 0.45)
    t0 = time.time()
    if engine == "whisper":
        res = _transcribe_openai_whisper(media, model, language, device, beam_size)
    else:
        engine = "faster-whisper"
        res = _transcribe_faster_whisper(media, model, language, device, beam_size, vad_filter)
    runtime = time.time() - t0

    report("writing", 0.9)
    meta = {
        "engine": engine,
        "model": model,
        "language": res.get("language", language),
        "device": device,
        "runtime_s": round(runtime, 1),
        "source_video": str(media),
    }
    written = core.write_outputs(
        item, res["segments"], res["text"], out_dir, outputs, interval, meta
    )

    if not keep_media and media.exists():
        try:
            media.unlink()
        except Exception:
            pass

    report("done", 1.0)
    return {"status": "done", "output_dir": str(out_dir), "outputs": written, **meta}
