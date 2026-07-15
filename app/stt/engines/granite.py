"""IBM Granite Speech 4.1 quality adapter (optional Transformers stack)."""
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


class GraniteEngine(BaseEngine):
    name = "granite"
    display_name = "Granite Speech 4.1"
    family = "granite"
    DEFAULT_MODEL = "ibm-granite/granite-speech-4.1-2b"

    def probe(self) -> Dict[str, Any]:
        installed = _have("transformers") and _have("torch")
        return {
            "installed": installed,
            "ready": installed,
            "package": "transformers+torch",
            "default_model": self.DEFAULT_MODEL,
            "notes": "Install via requirements-stt-quality.txt",
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            batch=True,
            word_timestamps=False,
            language_id=False,
            keyword_bias=True,
            languages=["en", "fr", "de", "es", "pt", "it", "ja", "zh"],
        )

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        if not (_have("transformers") and _have("torch")):
            raise EngineUnavailable(
                "Granite requires transformers+torch (pip install -r requirements-stt-quality.txt)"
            )
        model_id = request.model or self.DEFAULT_MODEL
        device = resolve_device(request.device)
        t0 = time.time()
        try:
            import torch
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        except Exception as e:
            raise EngineUnavailable(str(e)) from e

        try:
            dtype = torch.float16 if device == "cuda" else torch.float32
            processor = AutoProcessor.from_pretrained(model_id)
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                model_id, torch_dtype=dtype
            )
            model.to(device if device == "cuda" else "cpu")
            # Prefer pipeline when available for audio files.
            try:
                from transformers import pipeline
                pipe = pipeline(
                    "automatic-speech-recognition",
                    model=model,
                    tokenizer=processor.tokenizer,
                    feature_extractor=processor.feature_extractor,
                    device=0 if device == "cuda" else -1,
                    torch_dtype=dtype,
                    return_timestamps="word" if request.word_timestamps else True,
                )
                gen_kwargs: Dict[str, Any] = {}
                if request.vocabulary:
                    # Keyword bias via prompt / generation prompt when supported.
                    gen_kwargs["prompt"] = ", ".join(request.vocabulary[:32])
                out = pipe(path, generate_kwargs=gen_kwargs or None)
            except Exception:
                # Fallback: treat as single-utterance transcription via processor API.
                import soundfile as sf
                audio, sr = sf.read(path)
                inputs = processor(audio, sampling_rate=sr, return_tensors="pt")
                inputs = {k: v.to(model.device) for k, v in inputs.items()}
                with torch.no_grad():
                    ids = model.generate(**inputs, max_new_tokens=512)
                text = processor.batch_decode(ids, skip_special_tokens=True)[0]
                out = {"text": text, "chunks": [{"timestamp": (0.0, None), "text": text}]}
        except Exception as e:
            msg = str(e).lower()
            if "out of memory" in msg or "oom" in msg:
                raise EngineOOM(str(e)) from e
            raise EngineUnavailable(f"Granite transcription failed: {e}") from e

        text = (out.get("text") if isinstance(out, dict) else str(out) or "").strip()
        chunks = (out.get("chunks") if isinstance(out, dict) else None) or []
        segments = []
        if chunks:
            for i, ch in enumerate(chunks, start=1):
                ts = ch.get("timestamp") or (0.0, 0.0)
                start = float(ts[0] or 0.0)
                end = float(ts[1] if ts[1] is not None else start + 1.0)
                segments.append(Segment(id=i, start=start, end=end, text=(ch.get("text") or "").strip()))
        if not segments and text:
            segments = [Segment(id=1, start=0.0, end=0.0, text=text)]

        return STTResult(
            segments=segments,
            text=text or " ".join(s.text for s in segments),
            language=request.language if request.language != "auto" else "en",
            engine=self.name,
            model=model_id,
            device=device,
            timing_source=TimingSource.NATIVE.value if chunks else TimingSource.NONE.value,
            metrics={"runtime_s": round(time.time() - t0, 2)},
            raw_provenance={"keyword_bias": bool(request.vocabulary)},
        )
