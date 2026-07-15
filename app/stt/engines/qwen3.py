"""Qwen3-ASR multilingual adapter (Transformers backend on Windows)."""
from __future__ import annotations

import importlib.util
import time
from typing import Any, Dict

from ..base import BaseEngine, EngineOOM, EngineUnavailable
from ..hardware import resolve_device
from ..types import EngineCapabilities, Segment, STTRequest, STTResult, TimingSource


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


class Qwen3Engine(BaseEngine):
    name = "qwen3"
    display_name = "Qwen3-ASR"
    family = "qwen"
    DEFAULT_MODEL = "Qwen/Qwen3-ASR-1.7B"

    def probe(self) -> Dict[str, Any]:
        installed = _have("transformers") and _have("torch")
        return {
            "installed": installed,
            "ready": installed,
            "package": "transformers+torch",
            "default_model": self.DEFAULT_MODEL,
            "notes": "No vLLM on Windows — Transformers backend only.",
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            batch=True,
            language_id=True,
            word_timestamps=False,
            languages=[],  # 52 languages
        )

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        if not (_have("transformers") and _have("torch")):
            raise EngineUnavailable(
                "Qwen3-ASR requires transformers+torch (requirements-stt-quality.txt)"
            )
        model_id = request.model or self.DEFAULT_MODEL
        device = resolve_device(request.device)
        t0 = time.time()
        try:
            import torch
            from transformers import pipeline
            dtype = torch.float16 if device == "cuda" else torch.float32
            pipe = pipeline(
                "automatic-speech-recognition",
                model=model_id,
                device=0 if device == "cuda" else -1,
                torch_dtype=dtype,
                return_timestamps=True,
            )
            gen_kwargs: Dict[str, Any] = {}
            if request.language and request.language != "auto":
                gen_kwargs["language"] = request.language
            ctx = request.initial_prompt or ""
            if request.vocabulary:
                ctx = (ctx + " " + ", ".join(request.vocabulary[:48])).strip()
            if ctx:
                gen_kwargs["prompt"] = ctx
            out = pipe(path, generate_kwargs=gen_kwargs or None)
        except Exception as e:
            msg = str(e).lower()
            if "out of memory" in msg or "oom" in msg:
                raise EngineOOM(str(e)) from e
            raise EngineUnavailable(f"Qwen3 transcription failed: {e}") from e

        text = (out.get("text") if isinstance(out, dict) else str(out) or "").strip()
        chunks = (out.get("chunks") if isinstance(out, dict) else None) or []
        segments = []
        for i, ch in enumerate(chunks, start=1):
            ts = ch.get("timestamp") or (0.0, 0.0)
            start = float(ts[0] or 0.0)
            end = float(ts[1] if len(ts) > 1 and ts[1] is not None else start + 1.0)
            segments.append(Segment(
                id=i, start=start, end=end,
                text=(ch.get("text") or "").strip(),
                language=request.language if request.language != "auto" else None,
            ))
        if not segments and text:
            segments = [Segment(id=1, start=0.0, end=0.0, text=text)]

        return STTResult(
            segments=segments,
            text=text or " ".join(s.text for s in segments),
            language=request.language if request.language != "auto" else "",
            engine=self.name,
            model=model_id,
            device=device,
            timing_source=TimingSource.NATIVE.value if chunks else TimingSource.NONE.value,
            metrics={"runtime_s": round(time.time() - t0, 2)},
        )
