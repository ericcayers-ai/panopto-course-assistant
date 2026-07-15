"""FireRedASR Mandarin/dialect specialist adapter (optional)."""
from __future__ import annotations

import importlib.util
from typing import Any, Dict

from ..base import BaseEngine, EngineUnavailable
from ..types import EngineCapabilities, Segment, STTRequest, STTResult, TimingSource


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


class FireRedEngine(BaseEngine):
    name = "firered"
    display_name = "FireRedASR2-AED"
    family = "firered"
    DEFAULT_MODEL = "FireRedTeam/FireRedASR2-AED"

    def probe(self) -> Dict[str, Any]:
        installed = _have("fireredasr") or _have("fire_red_asr")
        return {
            "installed": installed,
            "ready": installed,
            "specialist": True,
            "default_model": self.DEFAULT_MODEL,
            "notes": "Not in default install; register when Chinese family is detected.",
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(batch=True, languages=["zh", "yue", "wuu"])

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        if not (_have("fireredasr") or _have("fire_red_asr")):
            raise EngineUnavailable(
                "FireRedASR specialist pack not installed (requirements-stt-specialist.txt)"
            )
        # Package APIs differ; keep a thin best-effort path.
        raise EngineUnavailable(
            "FireRedASR adapter is registered but the installed package API is unsupported. "
            "Use Qwen3 or faster-whisper until the specialist runtime is packaged."
        )
