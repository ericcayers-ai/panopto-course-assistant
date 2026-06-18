"""
transcribe.py - optional download + transcription pipeline.

Heavy dependencies (torch, whisper, faster-whisper, yt-dlp) are imported lazily
so that the web app starts and serves the feed/search/PDF features even when the
transcription stack is not installed. Use ``engine_status()`` to report what is
available to the frontend.
"""
from __future__ import annotations

import importlib.util
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import core
from .core import LectureItem, ensure_dir


def _cpu_threads() -> int:
    """Leave a couple of cores free so the desktop stays usable during CPU
    transcription. Override with PANOPTO_CPU_THREADS."""
    env = os.environ.get("PANOPTO_CPU_THREADS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 4) - 2)


# A loaded Whisper model is large; reuse it across jobs instead of reloading it
# (and re-allocating VRAM) for every lecture. Keyed by (model, device, compute).
_MODEL_CACHE: Dict[tuple, Any] = {}
_MODEL_LOCK = threading.Lock()


def _transcribe_concurrency() -> int:
    """How many lectures may run the Whisper model at the same time. One by
    default so a single GPU or modest amount of RAM is never asked to hold two
    models at once. Override with PANOPTO_TRANSCRIBE_CONCURRENCY."""
    env = os.environ.get("PANOPTO_TRANSCRIBE_CONCURRENCY")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return 1


# Downloads and other job types run in parallel; only the heavy transcription
# step passes through this gate, so memory stays safe while the queue still moves.
_TRANSCRIBE_SEM = threading.Semaphore(_transcribe_concurrency())


def _faster_whisper_model(model_name: str, device: str):
    compute_type = "float16" if device == "cuda" else "int8"
    key = (model_name, device, compute_type)
    with _MODEL_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            from faster_whisper import WhisperModel

            kwargs: Dict[str, Any] = {}
            if device != "cuda":
                kwargs["cpu_threads"] = _cpu_threads()
            model = WhisperModel(model_name, device=device, compute_type=compute_type, **kwargs)
            _MODEL_CACHE[key] = model
        return model


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
    via = _cuda_backend()
    return {
        "engines": engines,
        "any_engine": any(engines.values()),
        "yt_dlp": _have("yt_dlp"),
        "requests": _have("requests"),
        "markitdown": _have("markitdown"),
        "torch": _have("torch"),
        "cuda": via is not None,
        "cuda_via": via,                 # "ctranslate2" | "torch" | None - for diagnostics
        "default_engine": "faster-whisper" if engines["faster-whisper"] else (
            "whisper" if engines["whisper"] else None
        ),
    }


def recommend_settings() -> Dict[str, Any]:
    """Best transcription settings for the current machine - used by Simple mode's
    "auto-transcribe with best detected settings". Picks the strongest installed
    engine, the right device, and a model size that suits CPU-vs-GPU so the user
    needn't choose. Returns ``ready=False`` with a reason when no engine exists."""
    st = engine_status()
    engine = st["default_engine"]
    if not engine:
        return {"ready": False,
                "reason": "No transcription engine installed (faster-whisper / whisper).",
                "engine": None}
    cuda = st["cuda"]
    # On a GPU a larger model is affordable; on CPU keep it responsive.
    model = "medium" if cuda else "small"
    return {
        "ready": True,
        "engine": engine,
        "device": "cuda" if cuda else "cpu",
        "model": model,
        "language": "en",
        "interval": 30,           # progress/segment cadence (seconds)
        "rationale": (f"{engine} on {'GPU (CUDA)' if cuda else 'CPU'} with the "
                      f"{model} model - best speed/accuracy for this machine."),
    }


def _cuda_backend() -> Optional[str]:
    """Which backend can see a CUDA GPU, or None.

    The recommended engine, faster-whisper, runs on **CTranslate2**, which ships
    its own CUDA runtime and does *not* depend on PyTorch - so checking torch
    alone wrongly reports "CPU only" on machines that have a working GPU but no
    torch installed. Probe CTranslate2 first, then fall back to torch (used by
    the optional openai-whisper engine).
    """
    if _have("ctranslate2"):
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "ctranslate2"
        except Exception:
            pass
    if _have("torch"):
        try:
            import torch

            if torch.cuda.is_available():
                return "torch"
        except Exception:
            pass
    return None


def _cuda_available() -> bool:
    return _cuda_backend() is not None


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
    audio_only: bool = False,
) -> Path:
    """Direct HTTP download with a yt-dlp fallback.

    When ``audio_only`` is set, yt-dlp extracts an MP3 (smaller download, needs
    ffmpeg + yt-dlp). If yt-dlp is unavailable it falls back to a full download.
    """
    import requests

    ensure_dir(dest.parent)
    if audio_only and _have("yt_dlp"):
        return _download_audio(url, dest, cookies)
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


def _download_audio(url: str, dest: Path, cookies: str = "") -> Path:
    """Extract audio only (MP3) via yt-dlp to save bandwidth."""
    import yt_dlp

    target = dest.with_suffix(".mp3")
    opts: Dict[str, Any] = {
        "outtmpl": str(dest.with_suffix("")),  # yt-dlp appends the codec ext
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
    }
    if cookies:
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return target if target.exists() else dest


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


def _transcribe_faster_whisper(media: Path, model_name: str, language: str, device: str,
                               beam_size: int, vad_filter: bool,
                               progress: Optional[Callable[[float], None]] = None,
                               progress_period: float = 30.0) -> Dict[str, Any]:
    try:
        model = _faster_whisper_model(model_name, device)
    except Exception:
        # GPU was requested but its libraries are missing/incompatible - don't
        # fail the whole job, fall back to CPU and record the real device used.
        if device == "cuda":
            model = _faster_whisper_model(model_name, "cpu")
            device = "cpu"
        else:
            raise
    segments_iter, info = model.transcribe(
        str(media),
        language=language or None,
        beam_size=beam_size,
        vad_filter=vad_filter,
        condition_on_previous_text=True,
    )
    # faster-whisper yields segments lazily, so we can report how far through the
    # audio we are (seg.end / total duration). Throttled to ~progress_period
    # seconds of wall-clock so the percentage advances at least every 30s on a
    # long lecture without spamming the job store.
    total_dur = float(getattr(info, "duration", 0.0) or 0.0)
    last_emit = time.time()
    segments, parts = [], []
    for seg in segments_iter:
        t = seg.text.strip()
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": t})
        parts.append(t)
        if progress and total_dur:
            now = time.time()
            if now - last_emit >= progress_period:
                progress(min(0.99, float(seg.end) / total_dur))
                last_emit = now
    return {
        "segments": segments,
        "text": " ".join(p for p in parts if p).strip(),
        "language": getattr(info, "language", None) or language or "",
        "device": device,
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
    audio_only: bool = False,
    skip_existing: bool = True,
    force: bool = False,
    cookies: str = "",
    course: str = "",
    video_url: str = "",
    progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    outputs = [o for o in (outputs or ["txt", "srt", "md", "json"]) if o in core.OUTPUT_CHOICES] or ["txt"]
    device = resolve_device(device)

    def report(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    out_dir = core.output_dir_for(output_root, item, organize)
    ensure_dir(out_dir)
    stem = item.safe_title

    # Skip if every requested output already exists (unless forced).
    def output_path(fmt: str) -> Path:
        if fmt == "notebooklm":
            return out_dir / f"{stem}.notebooklm.md"
        if fmt == "summary":
            return out_dir / f"{stem}.summary.md"
        return out_dir / f"{stem}.{fmt}"

    if skip_existing and not force and all(output_path(o).exists() for o in outputs):
        report("done", 1.0)
        return {"status": "skipped", "reason": "outputs already exist", "output_dir": str(out_dir)}

    media = out_dir / f"{stem}.mp4"
    report("downloading", 0.0)
    media = download_media(
        item.url, media, cookies=cookies,
        progress=lambda f: report("downloading", f * 0.4), audio_only=audio_only,
    )

    # Serialise the heavy model step. The download above already ran in parallel;
    # here we wait for a free slot, reporting "waiting" each second so the job
    # shows why it is paused and can still be cancelled cooperatively.
    while not _TRANSCRIBE_SEM.acquire(timeout=1.0):
        report("waiting", 0.44)
    try:
        report("transcribing", 0.45)
        t0 = time.time()
        if engine == "whisper":
            res = _transcribe_openai_whisper(media, model, language, device, beam_size)
        else:
            engine = "faster-whisper"
            # Map the audio-position fraction into the 0.45 to 0.9 transcription
            # band so the job's percentage climbs steadily (refreshed ~every
            # `interval` seconds).
            res = _transcribe_faster_whisper(
                media, model, language, device, beam_size, vad_filter,
                progress=lambda f: report("transcribing", 0.45 + f * 0.45),
                progress_period=float(interval or 30))
        runtime = time.time() - t0
    finally:
        _TRANSCRIBE_SEM.release()

    report("writing", 0.9)
    meta = {
        "engine": engine,
        "model": model,
        "language": res.get("language", language),
        "device": res.get("device", device),     # the engine may have fallen back to CPU
        "runtime_s": round(runtime, 1),
        "source_video": str(media),
        "course": course,
        # The mp4 recording URL, kept so the SRT export can fetch the video on
        # demand even though we transcribed from the smaller audio download.
        "video_url": video_url,
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
