"""NVIDIA Parakeet TDT fast-batch adapter (optional NeMo)."""
from __future__ import annotations

import importlib.util
import time
from typing import Any, Dict

from ..base import BaseEngine, EngineOOM, EngineUnavailable
from ..hardware import resolve_device
from ..types import EngineCapabilities, Segment, STTRequest, STTResult, TimingSource, Word


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


class ParakeetEngine(BaseEngine):
    name = "parakeet"
    display_name = "Parakeet TDT 0.6B v3"
    family = "parakeet"
    DEFAULT_MODEL = "nvidia/parakeet-tdt-0.6b-v3"

    def probe(self) -> Dict[str, Any]:
        installed = _have("nemo") or _have("nemo_toolkit")
        return {
            "installed": installed,
            "ready": installed,
            "package": "nemo_toolkit[asr]",
            "default_model": self.DEFAULT_MODEL,
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            batch=True,
            word_timestamps=True,
            languages=["en", "es", "fr", "de", "it", "pt", "pl", "ru", "uk", "nl",
                       "cs", "ro", "hu", "sk", "bg", "hr", "sv", "da", "fi", "el",
                       "lt", "lv", "et", "mt", "sl"],
        )

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        if not (_have("nemo") or _have("nemo_toolkit")):
            raise EngineUnavailable(
                "Parakeet requires NeMo (pip install -r requirements-stt-fast.txt)"
            )
        model_id = request.model or self.DEFAULT_MODEL
        device = resolve_device(request.device)
        t0 = time.time()
        try:
            import nemo.collections.asr as nemo_asr
            model = nemo_asr.models.ASRModel.from_pretrained(model_id)
            if device == "cuda":
                model = model.cuda()
            hyp = model.transcribe([path], timestamps=True)
        except Exception as e:
            msg = str(e).lower()
            if "out of memory" in msg or "oom" in msg:
                raise EngineOOM(str(e)) from e
            raise EngineUnavailable(f"Parakeet transcription failed: {e}") from e

        # NeMo return shapes vary; normalize.
        item = hyp[0] if isinstance(hyp, (list, tuple)) and hyp else hyp
        text = ""
        segments: list[Segment] = []
        if hasattr(item, "text"):
            text = (item.text or "").strip()
        elif isinstance(item, dict):
            text = (item.get("text") or "").strip()
        elif isinstance(item, str):
            text = item.strip()

        stamp = getattr(item, "timestamp", None) or (item.get("timestamp") if isinstance(item, dict) else None)
        if isinstance(stamp, dict):
            for i, seg in enumerate(stamp.get("segment") or stamp.get("word") or [], start=1):
                if isinstance(seg, dict):
                    start = float(seg.get("start", 0.0))
                    end = float(seg.get("end", start))
                    stxt = (seg.get("segment") or seg.get("word") or seg.get("text") or "").strip()
                    words = []
                    if "word" in (seg or {}) and isinstance(seg.get("word"), list):
                        pass
                    segments.append(Segment(id=i, start=start, end=end, text=stxt, words=words))
        if not segments and text:
            segments = [Segment(id=1, start=0.0, end=0.0, text=text)]

        return STTResult(
            segments=segments,
            text=text or " ".join(s.text for s in segments),
            language=request.language if request.language != "auto" else "",
            engine=self.name,
            model=model_id,
            device=device,
            timing_source=TimingSource.NATIVE.value,
            metrics={"runtime_s": round(time.time() - t0, 2)},
        )
