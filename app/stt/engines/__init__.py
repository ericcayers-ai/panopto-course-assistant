"""Engine adapters — lazy imports; missing deps never crash app import."""
from __future__ import annotations

from typing import Dict, List, Optional

from ..base import BaseEngine

_ENGINES: Dict[str, BaseEngine] = {}


def _build() -> Dict[str, BaseEngine]:
    from .faster_whisper import FasterWhisperEngine
    from .openai_whisper import OpenAIWhisperEngine
    from .granite import GraniteEngine
    from .qwen3 import Qwen3Engine
    from .parakeet import ParakeetEngine
    from .moonshine import MoonshineEngine
    from .firered import FireRedEngine
    from .omnilingual import OmnilingualEngine

    engines: List[BaseEngine] = [
        FasterWhisperEngine(),
        OpenAIWhisperEngine(),
        GraniteEngine(),
        Qwen3Engine(),
        ParakeetEngine(),
        MoonshineEngine(),
        FireRedEngine(),
        OmnilingualEngine(),
    ]
    return {e.name: e for e in engines}


def get_engine(name: str) -> BaseEngine:
    global _ENGINES
    if not _ENGINES:
        _ENGINES = _build()
    # aliases
    aliases = {
        "fw": "faster-whisper",
        "faster_whisper": "faster-whisper",
        "openai-whisper": "whisper",
        "openai_whisper": "whisper",
    }
    key = aliases.get(name, name)
    if key not in _ENGINES:
        raise KeyError(f"Unknown STT engine: {name}")
    return _ENGINES[key]


def list_engines() -> List[BaseEngine]:
    global _ENGINES
    if not _ENGINES:
        _ENGINES = _build()
    return list(_ENGINES.values())


def availability_map() -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for eng in list_engines():
        try:
            out[eng.name] = eng.is_ready()
        except Exception:
            out[eng.name] = False
    return out


def legacy_engine_status() -> Dict[str, bool]:
    """Subset used by the compatibility facade /api/status engines dict."""
    m = availability_map()
    return {
        "faster-whisper": bool(m.get("faster-whisper")),
        "whisper": bool(m.get("whisper")),
        "granite": bool(m.get("granite")),
        "qwen3": bool(m.get("qwen3")),
        "parakeet": bool(m.get("parakeet")),
        "moonshine": bool(m.get("moonshine")),
        "firered": bool(m.get("firered")),
        "omnilingual": bool(m.get("omnilingual")),
    }
