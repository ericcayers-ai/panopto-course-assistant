"""OpenAI whisper adapter (optional legacy engine)."""
from __future__ import annotations

import importlib.util
import time
from typing import Any, Dict

from ..base import BaseEngine, EngineUnavailable
from ..hardware import resolve_device
from ..types import EngineCapabilities, Segment, STTRequest, STTResult, TimingSource


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


class OpenAIWhisperEngine(BaseEngine):
    name = "whisper"
    display_name = "OpenAI Whisper"
    family = "whisper"

    def probe(self) -> Dict[str, Any]:
        installed = _have("whisper")
        return {"installed": installed, "ready": installed, "package": "openai-whisper"}

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(batch=True, word_timestamps=True, language_id=True, languages=[])

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        if not _have("whisper"):
            raise EngineUnavailable("openai-whisper is not installed")
        import whisper
        device = resolve_device(request.device)
        model_name = request.model or "small"
        if model_name == "large":
            model_name = "large-v3"
        t0 = time.time()
        model = whisper.load_model(model_name, device=device)
        lang = None if (not request.language or request.language == "auto") else request.language
        result = model.transcribe(
            str(path),
            language=lang,
            verbose=False,
            fp16=(device == "cuda"),
            beam_size=request.beam_size,
            condition_on_previous_text=True,
            initial_prompt=request.initial_prompt or None,
        )
        segments = [
            Segment(
                id=i,
                start=float(s["start"]),
                end=float(s["end"]),
                text=(s.get("text") or "").strip(),
            )
            for i, s in enumerate(result.get("segments") or [], start=1)
        ]
        return STTResult(
            segments=segments,
            text=(result.get("text") or "").strip(),
            language=result.get("language") or request.language or "",
            engine=self.name,
            model=model_name,
            device=device,
            timing_source=TimingSource.NATIVE.value,
            metrics={"runtime_s": round(time.time() - t0, 2)},
        )
