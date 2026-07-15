"""Meta Omnilingual ASR long-tail specialist adapter (optional)."""
from __future__ import annotations

import importlib.util
from typing import Any, Dict

from ..base import BaseEngine, EngineUnavailable
from ..types import EngineCapabilities, STTRequest, STTResult


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


class OmnilingualEngine(BaseEngine):
    name = "omnilingual"
    display_name = "Meta Omnilingual ASR"
    family = "omnilingual"
    DEFAULT_MODEL = "facebook/omnilingual-asr"

    def probe(self) -> Dict[str, Any]:
        installed = _have("omnilingual") or _have("fairseq")
        return {
            "installed": installed,
            "ready": installed,
            "specialist": True,
            "default_model": self.DEFAULT_MODEL,
            "notes": "Long-tail language pack; not in default install.",
        }

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(batch=True, languages=[])

    def transcribe_file(self, path: str, request: STTRequest) -> STTResult:
        raise EngineUnavailable(
            "Omnilingual ASR specialist pack not installed / not wired for this runtime. "
            "Install requirements-stt-specialist.txt when needed; router falls back to Qwen3/faster-whisper."
        )
