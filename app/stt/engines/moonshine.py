"""Moonshine streaming / edge adapter for live + eco modes."""
from __future__ import annotations

import importlib.util
import time
from typing import Any, Dict, Iterator, List

from ..base import BaseEngine, EngineUnavailable, StreamSession
from ..types import EngineCapabilities, Segment, STTRequest, STTResult, TimingSource, Word


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


class _MoonshineStream(StreamSession):
    def __init__(self, model: Any, request: STTRequest):
        self.model = model
        self.request = request
        self._parts: List[str] = []
        self._segments: List[Segment] = []
        self._t0 = 0.0
        self._seq = 0

    def feed_audio(self, pcm16le: bytes, sample_rate: int = 16000) -> Iterator[Dict[str, Any]]:
        self._seq += 1
        # usefulsensors moonshine APIs vary; best-effort.
        text = ""
        try:
            if hasattr(self.model, "transcribe_stream"):
                text = self.model.transcribe_stream(pcm16le, sample_rate) or ""
            elif hasattr(self.model, "generate"):
                text = self.model.generate(pcm16le) or ""
        except Exception as e:
            yield {"event": "error", "seq": self._seq, "error": str(e)}
            return
        text = (text or "").strip()
        if text:
            self._parts.append(text)
            yield {
                "event": "provisional",
                "seq": self._seq,
                "text": text,
                "final": False,
            }

    def finalize(self) -> STTResult:
        text = " ".join(self._parts).strip()
        segs = list(self._segments)
        if text and not segs:
            segs = [Segment(id=1, start=0.0, end=0.0, text=text)]
        return STTResult(
            segments=segs,
            text=text,
            language="en",
            engine="moonshine",
            model=self.request.model or "usefulsensors/moonshine-streaming-medium",
            device="cpu",
            timing_source=TimingSource.NATIVE.value,
        )


class MoonshineEngine(BaseEngine):
    name = "moonshine"
    display_name = "Moonshine Streaming"
    family = "moonshine"
    DEFAULT_MODEL = "usefulsensors/moonshine-streaming-medium"

    def probe(self) -> Dict[str, Any]:
        installed = _have("moonshine") or _have("moonshine_onnx") or _have("useful_moonshine")
        return {
            "installed": installed,
            "ready": installed,
            "package": "useful-moonshine / moonshine",
            "streaming": True,
            "default_model": self.DEFAULT_MODEL,
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            batch=True,
            streaming=True,
            word_timestamps=True,
            languages=["en"],
        )

    def _load(self, model_id: str):
        # Try known package entry points.
        errors = []
        for mod_name, attr in (
            ("moonshine", "load_model"),
            ("moonshine_onnx", "load_model"),
            ("useful_moonshine", "load_model"),
        ):
            if not _have(mod_name):
                continue
            try:
                mod = __import__(mod_name)
                loader = getattr(mod, attr, None)
                if loader:
                    return loader(model_id)
                if hasattr(mod, "MoonshineOnnxModel"):
                    return mod.MoonshineOnnxModel(model_name=model_id)
            except Exception as e:
                errors.append(f"{mod_name}: {e}")
        raise EngineUnavailable(
            "Moonshine not available. Install requirements-stt-live.txt. "
            + "; ".join(errors[:2])
        )

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        model_id = request.model or self.DEFAULT_MODEL
        t0 = time.time()
        model = self._load(model_id)
        text = ""
        try:
            if hasattr(model, "transcribe"):
                out = model.transcribe(path)
                text = out if isinstance(out, str) else (out.get("text") if isinstance(out, dict) else str(out))
            else:
                raise EngineUnavailable("Moonshine model has no transcribe()")
        except EngineUnavailable:
            raise
        except Exception as e:
            raise EngineUnavailable(f"Moonshine transcription failed: {e}") from e
        text = (text or "").strip()
        return STTResult(
            segments=[Segment(id=1, start=0.0, end=0.0, text=text)] if text else [],
            text=text,
            language="en",
            engine=self.name,
            model=model_id,
            device="cpu",
            timing_source=TimingSource.NATIVE.value,
            metrics={"runtime_s": round(time.time() - t0, 2)},
        )

    def start_stream(self, request: STTRequest) -> StreamSession:
        model = self._load(request.model or self.DEFAULT_MODEL)
        return _MoonshineStream(model, request)
