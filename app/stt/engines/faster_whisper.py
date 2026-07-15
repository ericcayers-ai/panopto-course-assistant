"""faster-whisper adapter — production universal fallback."""
from __future__ import annotations

import importlib.util
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..base import BaseEngine, EngineOOM, EngineUnavailable
from ..hardware import resolve_device
from ..types import EngineCapabilities, Segment, STTRequest, STTResult, TimingSource, Word

_MODEL_CACHE: Dict[tuple, Any] = {}
_MODEL_LOCK = threading.Lock()


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _cpu_threads() -> int:
    env = os.environ.get("PANOPTO_CPU_THREADS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 4) - 2)


class FasterWhisperEngine(BaseEngine):
    name = "faster-whisper"
    display_name = "faster-whisper"
    family = "whisper"

    def probe(self) -> Dict[str, Any]:
        installed = _have("faster_whisper")
        return {
            "installed": installed,
            "ready": installed,
            "package": "faster-whisper",
            "streaming": False,
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            batch=True,
            streaming=False,
            word_timestamps=True,
            language_id=True,
            keyword_bias=True,
            languages=[],
            offline=True,
        )

    def _load_model(self, model_name: str, device: str, compute_type: str):
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

    def unload(self) -> None:
        with _MODEL_LOCK:
            _MODEL_CACHE.clear()

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        if not _have("faster_whisper"):
            raise EngineUnavailable("faster-whisper is not installed")
        device = resolve_device(request.device)
        model_name = request.model or "small"
        compute = (request.compute or "auto").lower()
        if compute == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        else:
            compute_type = compute

        try:
            model = self._load_model(model_name, device, compute_type)
        except Exception as e:
            if device == "cuda":
                device = "cpu"
                compute_type = "int8"
                try:
                    model = self._load_model(model_name, device, compute_type)
                except Exception:
                    raise EngineUnavailable(str(e)) from e
            else:
                msg = str(e).lower()
                if "out of memory" in msg or "cuda" in msg and "memory" in msg:
                    raise EngineOOM(str(e)) from e
                raise

        lang = None if (not request.language or request.language == "auto") else request.language
        prompt = request.initial_prompt or ""
        if request.vocabulary and not prompt:
            prompt = " " + ", ".join(request.vocabulary[:64])
        hotwords = request.hotwords or (" ".join(request.vocabulary[:32]) if request.vocabulary else None)

        t0 = time.time()
        kwargs: Dict[str, Any] = {
            "language": lang,
            "beam_size": request.beam_size,
            "vad_filter": request.vad_filter,
            "condition_on_previous_text": True,
            "word_timestamps": bool(request.word_timestamps),
        }
        if prompt:
            kwargs["initial_prompt"] = prompt
        if hotwords:
            try:
                kwargs["hotwords"] = hotwords
            except Exception:
                pass

        try:
            segments_iter, info = model.transcribe(str(path), **kwargs)
        except TypeError:
            kwargs.pop("hotwords", None)
            segments_iter, info = model.transcribe(str(path), **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "out of memory" in msg or "oom" in msg:
                raise EngineOOM(str(e)) from e
            raise

        segments: list[Segment] = []
        parts = []
        for i, seg in enumerate(segments_iter, start=1):
            text = (seg.text or "").strip()
            words = []
            if request.word_timestamps and getattr(seg, "words", None):
                for w in seg.words:
                    words.append(Word(
                        word=(w.word or "").strip(),
                        start=float(w.start or 0.0),
                        end=float(w.end or 0.0),
                        confidence=float(w.probability) if getattr(w, "probability", None) is not None else None,
                    ))
            segments.append(Segment(
                id=i, start=float(seg.start), end=float(seg.end), text=text,
                language=getattr(info, "language", None) or request.language or None,
                words=words,
            ))
            if text:
                parts.append(text)

        return STTResult(
            segments=segments,
            text=" ".join(parts).strip(),
            language=getattr(info, "language", None) or request.language or "",
            engine=self.name,
            model=model_name,
            device=device,
            timing_source=TimingSource.NATIVE.value,
            metrics={"runtime_s": round(time.time() - t0, 2), "compute_type": compute_type},
        )


def transcribe_legacy(
    media: Path,
    model_name: str,
    language: str,
    device: str,
    beam_size: int,
    vad_filter: bool,
    progress: Optional[Callable[[float], None]] = None,
    progress_period: float = 30.0,
) -> Dict[str, Any]:
    """Compatibility helper matching the old _transcribe_faster_whisper signature."""
    eng = FasterWhisperEngine()
    req = STTRequest(
        media_path=str(media),
        model=model_name,
        language=language or "en",
        device=device,
        beam_size=beam_size,
        vad_filter=vad_filter,
        word_timestamps=False,
        profile="auto",
    )
    # Inline progress-aware path for jobs that expect progress callbacks.
    if not _have("faster_whisper"):
        raise EngineUnavailable("faster-whisper is not installed")
    device = resolve_device(device)
    compute_type = "float16" if device == "cuda" else "int8"
    try:
        model = eng._load_model(model_name, device, compute_type)
    except Exception:
        if device == "cuda":
            model = eng._load_model(model_name, "cpu", "int8")
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
