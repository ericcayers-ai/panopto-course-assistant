"""End-to-end STT pipeline: captions → preprocess → route → chunk → enrich."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import audio as audio_mod
from . import captions as captions_mod
from . import checkpoint as ckpt
from . import chunking
from . import enrichment
from . import engines
from . import router as router_mod
from .base import EngineOOM, EngineUnavailable
from .hardware import probe_hardware, resolve_device
from .types import SCHEMA_VERSION, STTRequest, STTResult, Segment
from .workers import WorkerClient


ProgressCb = Optional[Callable[[str, float], None]]

# Heavy families run in a subprocess by default so Torch/NeMo/Moonshine stay
# out of the FastAPI process. Set PANOPTO_STT_INPROCESS=1 to force in-process.
_WORKER_ENGINES = frozenset({
    "granite", "qwen3", "parakeet", "moonshine", "firered", "omnilingual",
})


def _use_worker(engine_name: str) -> bool:
    if os.environ.get("PANOPTO_STT_INPROCESS", "").strip() in {"1", "true", "yes"}:
        return False
    if os.environ.get("PANOPTO_STT_WORKER", "1").strip() in {"0", "false", "no"}:
        return False
    return engine_name in _WORKER_ENGINES


def _report(progress: ProgressCb, stage: str, frac: float) -> None:
    if progress:
        progress(stage, max(0.0, min(1.0, frac)))


def available_engines() -> Dict[str, bool]:
    return engines.availability_map()


def transcribe_path(
    path: str,
    request: Optional[STTRequest] = None,
    *,
    progress: ProgressCb = None,
    work_dir: Optional[Path] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> STTResult:
    """Transcribe a local media file with adaptive routing + resume."""
    request = request or STTRequest()
    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(str(media))
    request.media_path = str(media)
    work = Path(work_dir or (media.parent / f".stt_work_{media.stem}"))
    work.mkdir(parents=True, exist_ok=True)

    # Caption-first
    if request.caption_first:
        _report(progress, "captions", 0.02)
        cap_result, cap_meta = captions_mod.try_caption_first(
            caption_url=request.caption_url,
            caption_path=str(request.extras.get("caption_path") or ""),
            cookies=request.cookies,
            work_dir=work,
        )
        if cap_result is not None:
            cap_result.preprocessing = cap_meta
            _report(progress, "done", 1.0)
            return cap_result

    hw = probe_hardware(str(work))
    avail = available_engines()
    decision = router_mod.route(
        request, hardware=hw, available=avail, has_usable_captions=False,
    )
    _report(progress, "preprocess", 0.05)

    # Preprocess when ffmpeg is available; otherwise transcribe the media as-is.
    pre: Dict[str, Any] = {}
    wav_path = media
    try:
        pre = audio_mod.preprocess_media(media, work)
        wav_path = Path(pre["wav"])
    except Exception as e:
        pre = {"error": str(e), "wav": str(media)}
        wav_path = media

    fingerprint = pre.get("fingerprint") or audio_mod.fingerprint_file(wav_path)
    settings_hash = ckpt.settings_fingerprint({
        "engine": decision.engine,
        "model": decision.model,
        "language": request.language,
        "profile": request.profile,
        "chunk_seconds": request.chunk_seconds,
        "diarization": request.diarization,
    })

    duration = float(pre.get("duration_s") or 0.0)
    plans = chunking.plan_chunks(
        duration or 1.0,
        pre.get("vad_regions"),
        max_seconds=float(request.chunk_seconds or 180),
    )

    partial = work / f"{media.stem}.stt.partial.json"
    data = None
    if request.resume:
        data = ckpt.load_checkpoint(partial)
        if data and (data.get("fingerprint") != fingerprint
                     or data.get("settings_hash") != settings_hash):
            data = None
    if data is None:
        data = ckpt.init_checkpoint(
            partial, fingerprint=fingerprint, settings_hash=settings_hash,
            chunks=plans, meta={"route": decision.to_dict()},
        )

    fallbacks_used: List[str] = []
    current = decision
    t0 = time.time()
    worker: Optional[WorkerClient] = None

    def run_engine(engine_name: str, model: str, chunk_wav: Path, offset: float) -> List[Segment]:
        nonlocal worker
        req = STTRequest.from_dict(request.to_dict())
        req.engine = engine_name
        req.model = model
        req.device = resolve_device(request.device, hw)
        req.media_path = str(chunk_wav)
        if _use_worker(engine_name):
            if worker is None:
                worker = WorkerClient()
            resp = worker.request(
                "transcribe_chunk",
                engine=engine_name,
                path=str(chunk_wav),
                request=req.to_dict(),
            )
            result = STTResult.from_dict(resp.get("result") or {})
        else:
            eng = engines.get_engine(engine_name)
            result = eng.transcribe_file(str(chunk_wav), req)
        return chunking.shift_segments(result.segments, offset)

    try:
        total = max(1, len(plans))
        while True:
            missing = ckpt.first_missing_chunk(data)
            if missing is None:
                break
            if cancel_check and cancel_check():
                raise RuntimeError("cancelled")
            plan = next(p for p in plans if p.index == missing)
            chunk_wav = wav_path
            offset = plan.start
            try:
                if (wav_path.suffix.lower() == ".wav"
                        and (plan.end - plan.start) < (duration or 1e9) - 0.5):
                    chunk_wav = work / f"chunk_{plan.index:04d}.wav"
                    audio_mod.slice_wav(wav_path, chunk_wav, plan.start, plan.end)
                    offset = plan.start
            except Exception:
                chunk_wav = wav_path
                offset = 0.0

            segs: List[Segment] = []
            attempt = current
            last_err: Optional[Exception] = None
            while attempt is not None:
                try:
                    if attempt.engine == "captions":
                        break
                    segs = run_engine(attempt.engine, attempt.model, chunk_wav, offset)
                    current = attempt
                    last_err = None
                    break
                except (EngineOOM, EngineUnavailable, Exception) as e:
                    last_err = e
                    token = f"{attempt.engine}:{attempt.model}"
                    fallbacks_used.append(token)
                    nxt = router_mod.next_fallback(attempt, failed=token)
                    if nxt is None:
                        break
                    if isinstance(e, EngineOOM) and request.compute != "int8":
                        request.compute = "int8"
                    attempt = nxt
            if last_err and not segs:
                raise last_err
            data = ckpt.save_chunk(partial, plan.index, segs)
            _report(progress, "transcribing", 0.1 + 0.7 * ((plan.index + 1) / total))
    finally:
        if worker is not None:
            try:
                worker.close()
            except Exception:
                pass

    pairs = ckpt.completed_pairs(data)
    merged = chunking.merge_chunk_segments(pairs)
    result = STTResult(
        segments=merged,
        text=" ".join(s.text for s in merged if s.text).strip(),
        language=request.language if request.language != "auto" else "",
        engine=current.engine,
        model=current.model,
        device=resolve_device(request.device, hw),
        schema_version=SCHEMA_VERSION,
        route_reason=current.reason,
        input_fingerprint=fingerprint,
        preprocessing=pre,
        metrics={"runtime_s": round(time.time() - t0, 2), "chunks": len(plans)},
        fallbacks_used=fallbacks_used,
        raw_provenance={"route": current.to_dict()},
    )

    _report(progress, "enriching", 0.88)
    hf_token = ""
    try:
        from .. import secrets as secret_store
        from .. import context as app_ctx
        root = getattr(app_ctx, "OUTPUT_DIR", None)
        if root is not None:
            hf_token = secret_store.get_secret("huggingface_token", root=root) or ""
    except Exception:
        pass

    result = enrichment.enrich(
        result,
        str(wav_path),
        word_timestamps=request.word_timestamps,
        diarization_mode=request.diarization,
        speakers=request.speakers,
        hf_token=hf_token,
        corrections=request.extras.get("corrections") if isinstance(request.extras, dict) else None,
        default_language="" if request.language == "auto" else request.language,
    )

    if request.resume:
        data = ckpt.load_checkpoint(partial) or data
        data["meta"] = {**(data.get("meta") or {}), "complete": True}
        ckpt._atomic_write(partial, data)

    _report(progress, "done", 0.95)
    return result


def recommend_for_machine() -> Dict[str, Any]:
    """Settings recommendation including adaptive profile routing."""
    hw = probe_hardware()
    avail = available_engines()
    ready = bool(avail.get("faster-whisper") or avail.get("whisper")
                 or avail.get("granite") or avail.get("qwen3")
                 or avail.get("parakeet") or avail.get("moonshine"))
    if not ready:
        return {
            "ready": False,
            "reason": "No transcription engine installed (faster-whisper / whisper / STT extras).",
            "engine": None,
            "profile": "auto",
            "hardware": hw.to_dict(),
            "engines": avail,
        }
    req = STTRequest(profile="auto", language="en", device="auto")
    decision = router_mod.route(req, hardware=hw, available=avail)
    return {
        "ready": True,
        "engine": decision.engine if decision.engine != "captions" else (
            "faster-whisper" if avail.get("faster-whisper") else "whisper"
        ),
        "model": decision.model if decision.engine != "captions" else (
            "large-v3-turbo" if hw.cuda else "small"
        ),
        "device": resolve_device("auto", hw),
        "language": "en",
        "profile": "auto",
        "interval": 30,
        "vram_mb": hw.vram_mb,
        "hardware": hw.to_dict(),
        "route": decision.to_dict(),
        "rationale": decision.reason,
        "engines": avail,
    }
