"""
transcribe.py - compatibility facade over the adaptive STT platform.

Heavy dependencies (torch, whisper, faster-whisper, yt-dlp, NeMo, Moonshine)
are imported lazily so the web app starts without the transcription stack.
Existing callers keep using ``engine_status``, ``recommend_settings``,
``download_media``, and ``transcribe_lecture`` with the same signatures.
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
    env = os.environ.get("PANOPTO_CPU_THREADS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 4) - 2)


_MODEL_CACHE: Dict[tuple, Any] = {}
_MODEL_LOCK = threading.Lock()


def _transcribe_concurrency() -> int:
    env = os.environ.get("PANOPTO_TRANSCRIBE_CONCURRENCY")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return 1


_TRANSCRIBE_SEM = threading.Semaphore(_transcribe_concurrency())


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _faster_whisper_model(model_name: str, device: str):
    """Retained for monkeypatches in older tests; delegates to the adapter cache."""
    from .stt.engines.faster_whisper import FasterWhisperEngine
    compute_type = "float16" if device == "cuda" else "int8"
    return FasterWhisperEngine()._load_model(model_name, device, compute_type)


def engine_status() -> Dict[str, Any]:
    """Report which transcription engines / helpers are installed."""
    from .stt.engines import legacy_engine_status
    from .stt.hardware import probe_hardware

    engines = legacy_engine_status()
    # Keep classic keys always present for older clients/tests.
    engines.setdefault("faster-whisper", False)
    engines.setdefault("whisper", False)
    hw = probe_hardware()
    via = hw.cuda_via
    any_classic = bool(engines.get("faster-whisper") or engines.get("whisper"))
    any_engine = any_classic or any(
        engines.get(k) for k in ("granite", "qwen3", "parakeet", "moonshine")
    )
    return {
        "engines": engines,
        "any_engine": any_engine,
        "yt_dlp": _have("yt_dlp"),
        "requests": _have("requests"),
        "markitdown": _have("markitdown"),
        "torch": _have("torch"),
        "cuda": via is not None,
        "cuda_via": via,
        "hardware": hw.to_dict(),
        "stt_profiles": ["auto", "quality", "fast", "live", "eco"],
        "default_engine": (
            "faster-whisper" if engines.get("faster-whisper") else (
                "whisper" if engines.get("whisper") else (
                    "granite" if engines.get("granite") else (
                        "qwen3" if engines.get("qwen3") else None
                    )
                )
            )
        ),
    }


def _gpu_vram_mb() -> int:
    from .stt.hardware import probe_hardware
    return int(probe_hardware().vram_mb or 0)


def recommend_settings() -> Dict[str, Any]:
    """Best transcription settings for the current machine (adaptive router)."""
    from .stt.pipeline import recommend_for_machine
    rec = recommend_for_machine()
    # Preserve classic keys expected by Moodle Quick / Simple mode UI.
    if not rec.get("ready"):
        return {
            "ready": False,
            "reason": rec.get("reason") or "No transcription engine installed.",
            "engine": None,
            "profile": "auto",
        }
    return {
        "ready": True,
        "engine": rec.get("engine"),
        "device": rec.get("device"),
        "model": rec.get("model"),
        "language": rec.get("language") or "en",
        "profile": rec.get("profile") or "auto",
        "interval": rec.get("interval", 30),
        "vram_mb": rec.get("vram_mb", 0),
        "rationale": rec.get("rationale") or "",
        "route": rec.get("route"),
        "hardware": rec.get("hardware"),
        "engines": rec.get("engines"),
    }


def _cuda_backend() -> Optional[str]:
    from .stt.hardware import probe_hardware
    return probe_hardware().cuda_via


def _cuda_available() -> bool:
    return _cuda_backend() is not None


def resolve_device(requested: str) -> str:
    from .stt.hardware import resolve_device as _resolve
    return _resolve(requested)


# ---------------------------------------------------------------------------
# Download (unchanged public API)
# ---------------------------------------------------------------------------


def download_media(
    url: str,
    dest: Path,
    cookies: str = "",
    progress: Optional[Callable[[float], None]] = None,
    audio_only: bool = False,
) -> Path:
    """Direct HTTP download with a yt-dlp fallback."""
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
    import yt_dlp

    target = dest.with_suffix(".mp3")
    opts: Dict[str, Any] = {
        "outtmpl": str(dest.with_suffix("")),
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
# Engines — legacy helpers kept for monkeypatch-based tests
# ---------------------------------------------------------------------------


def _transcribe_faster_whisper(media: Path, model_name: str, language: str, device: str,
                               beam_size: int, vad_filter: bool,
                               progress: Optional[Callable[[float], None]] = None,
                               progress_period: float = 30.0) -> Dict[str, Any]:
    """Legacy helper — uses ``_faster_whisper_model`` so unit tests can monkeypatch it."""
    model = _faster_whisper_model(model_name, device)
    segments_iter, info = model.transcribe(
        str(media),
        language=language or None,
        beam_size=beam_size,
        vad_filter=vad_filter,
        condition_on_previous_text=True,
    )
    total_dur = float(getattr(info, "duration", 0.0) or 0.0)
    last_emit = time.time()
    segments, parts = [], []
    for seg in segments_iter:
        t = (seg.text or "").strip()
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
        "model": model_name,
        "schema_version": 2,
    }


def _transcribe_openai_whisper(media: Path, model_name: str, language: str, device: str,
                               beam_size: int) -> Dict[str, Any]:
    from .stt.engines.openai_whisper import OpenAIWhisperEngine
    from .stt.types import STTRequest
    eng = OpenAIWhisperEngine()
    res = eng.transcribe_file(str(media), STTRequest(
        model=model_name, language=language or "en", device=device, beam_size=beam_size,
    ))
    return res.to_legacy_dict()


# ---------------------------------------------------------------------------
# Top-level pipeline for one lecture
# ---------------------------------------------------------------------------


def transcribe_lecture(
    item: LectureItem,
    output_root: Path,
    *,
    engine: str = "auto",
    model: str = "auto",
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
    # --- adaptive STT extensions (all optional / backward compatible) ---
    profile: str = "auto",
    code_switch: bool = False,
    word_timestamps: bool = True,
    diarization: str = "off",
    speakers: Optional[int] = None,
    vocabulary: Optional[List[str]] = None,
    caption_first: bool = True,
    caption_url: str = "",
    resume: bool = True,
    chunk_seconds: int = 180,
    compute: str = "auto",
    hotwords: str = "",
    initial_prompt: str = "",
    use_adaptive: bool = True,
    index_db: bool = True,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    outputs = [o for o in (outputs or ["txt", "srt", "md", "json"]) if o in core.OUTPUT_CHOICES] or ["txt"]
    device = resolve_device(device)

    def report(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    def _cancel_poll() -> bool:
        if cancel_check and cancel_check():
            return True
        # JobManager raises JobCancelled from progress ticks — poke it between chunks.
        if progress:
            try:
                progress("transcribing", None)  # type: ignore[arg-type]
            except TypeError:
                pass
            except Exception as exc:
                if exc.__class__.__name__ == "JobCancelled":
                    raise
                raise
        return False

    out_dir = core.output_dir_for(output_root, item, organize)
    ensure_dir(out_dir)
    stem = item.safe_title

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

    while not _TRANSCRIBE_SEM.acquire(timeout=1.0):
        report("waiting", 0.44)
    try:
        report("transcribing", 0.45)
        t0 = time.time()

        # Adaptive is the default. Legacy monkeypatchable helpers are opt-in via
        # profile=legacy or use_adaptive=False (keeps older unit tests green).
        force_legacy = (not use_adaptive) or str(profile or "").lower() == "legacy"

        # Caption-first short-circuit when a caption URL is already known.
        if caption_first and caption_url:
            try:
                from .stt import captions as captions_mod
                cap_res, cap_meta = captions_mod.try_caption_first(
                    caption_url=caption_url,
                    cookies=cookies,
                    work_dir=out_dir / f".stt_work_{stem}",
                )
                if cap_res is not None:
                    res = cap_res.to_legacy_dict()
                    engine_used = "captions"
                    route_reason = cap_res.route_reason
                    fallbacks_used = []
                    runtime = time.time() - t0
                    report("writing", 0.9)
                    meta = {
                        "schema_version": 2,
                        "engine": engine_used,
                        "model": res.get("model") or "panopto",
                        "language": res.get("language", language),
                        "device": device,
                        "runtime_s": round(runtime, 1),
                        "source_video": str(media),
                        "course": course,
                        "video_url": video_url,
                        "profile": profile or "auto",
                        "route_reason": route_reason,
                        "timing_source": res.get("timing_source") or "caption",
                        "preprocessing": cap_meta,
                    }
                    written = core.write_outputs(
                        item, res["segments"], res["text"], out_dir, outputs, interval, meta
                    )
                    if index_db:
                        try:
                            _index_transcript(item, out_dir, written, course, meta)
                        except Exception:
                            pass
                    if not keep_media and media.exists():
                        try:
                            media.unlink()
                        except Exception:
                            pass
                    report("done", 1.0)
                    return {"status": "done", "output_dir": str(out_dir), "outputs": written, **meta}
            except Exception:
                pass

        if force_legacy and engine in {"whisper", "openai-whisper", "openai_whisper"}:
            res = _transcribe_openai_whisper(
                media, model if model != "auto" else "small", language, device, beam_size,
            )
            engine_used = "whisper"
            route_reason = "Legacy openai-whisper path."
            fallbacks_used: List[str] = []
        elif force_legacy:
            engine_used = "faster-whisper"
            res = _transcribe_faster_whisper(
                media, model if model != "auto" else "small", language, device, beam_size, vad_filter,
                progress=lambda f: report("transcribing", 0.45 + f * 0.45),
                progress_period=2.0)
            route_reason = "Legacy faster-whisper path."
            fallbacks_used = []
        else:
            from .stt.pipeline import transcribe_path
            from .stt.types import STTRequest
            req = STTRequest(
                media_path=str(media),
                profile=profile or "auto",
                language=language or "auto",
                engine=None if engine in ("", "auto") else engine,
                model=model if model and model != "auto" else None,
                device=device,
                code_switch=code_switch,
                word_timestamps=word_timestamps,
                diarization=diarization or "off",
                speakers=speakers,
                vocabulary=list(vocabulary or []),
                caption_first=caption_first,
                caption_url=caption_url or "",
                resume=resume,
                chunk_seconds=chunk_seconds,
                compute=compute,
                hotwords=hotwords,
                initial_prompt=initial_prompt,
                beam_size=beam_size,
                vad_filter=vad_filter,
                cookies=cookies,
                course=course,
                extras={"caption_path": ""},
            )
            work = out_dir / f".stt_work_{stem}"
            stt_res = transcribe_path(
                str(media), req,
                progress=lambda s, f: report(s, 0.45 + f * 0.45),
                work_dir=work,
                cancel_check=_cancel_poll,
            )
            res = stt_res.to_legacy_dict()
            engine_used = stt_res.engine or engine or "faster-whisper"
            route_reason = stt_res.route_reason
            fallbacks_used = list(stt_res.fallbacks_used)
            # Clear checkpoint after successful publish.
            try:
                from .stt import checkpoint as ckpt
                ckpt.clear_checkpoint(work / f"{media.stem}.stt.partial.json")
            except Exception:
                pass

        runtime = time.time() - t0
    finally:
        _TRANSCRIBE_SEM.release()

    report("writing", 0.9)
    meta = {
        "schema_version": int(res.get("schema_version") or 2),
        "engine": engine_used,
        "model": res.get("model") or model,
        "language": res.get("language", language),
        "device": res.get("device", device),
        "runtime_s": round(runtime, 1),
        "source_video": str(media),
        "course": course,
        "video_url": video_url,
        "profile": profile or "auto",
        "route_reason": route_reason or res.get("route_reason") or "",
        "timing_source": res.get("timing_source") or "",
        "diarization_source": res.get("diarization_source") or "",
        "input_fingerprint": res.get("input_fingerprint") or "",
        "preprocessing": res.get("preprocessing") or {},
        "metrics": res.get("metrics") or {},
        "fallbacks_used": fallbacks_used or res.get("fallbacks_used") or [],
    }
    written = core.write_outputs(
        item, res["segments"], res["text"], out_dir, outputs, interval, meta
    )

    if index_db:
        try:
            _index_transcript(item, out_dir, written, course, meta)
        except Exception:
            pass

    if not keep_media and media.exists():
        try:
            media.unlink()
        except Exception:
            pass

    report("done", 1.0)
    return {"status": "done", "output_dir": str(out_dir), "outputs": written, **meta}


def _index_transcript(
    item: LectureItem,
    out_dir: Path,
    written: Dict[str, str],
    course: str,
    meta: Dict[str, Any],
) -> None:
    """Index the completed transcript in SQLite immediately when a DB is bound."""
    try:
        from . import context
        db = getattr(context, "db", None)
        if db is None:
            return
        json_path = written.get("json") or str(out_dir / f"{item.safe_title}.json")
        course_id = None
        if course:
            try:
                from . import settings_store
                # Resolve by name if possible; else leave unbound.
                rows = db.list_courses() if hasattr(db, "list_courses") else []
                for row in rows:
                    title = row["title"] if hasattr(row, "keys") else row[1]
                    if str(title).lower() == course.lower():
                        course_id = int(row["id"] if hasattr(row, "keys") else row[0])
                        break
            except Exception:
                course_id = None
        import json as _json
        db.insert_transcript(
            course_id, item.title, json_path,
            week=item.week, date=item.pub_date or "",
            duration=item.duration or None,
            metadata_json=_json.dumps({k: meta.get(k) for k in
                ("engine", "model", "language", "profile", "route_reason")}),
        )
    except Exception:
        return
