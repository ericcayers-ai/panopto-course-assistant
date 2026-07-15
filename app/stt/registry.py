"""Model metadata registry for adaptive STT routing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ModelSpec:
    engine: str
    model_id: str
    display_name: str
    profile_tags: tuple  # e.g. ("quality", "auto")
    languages: tuple  # empty = multilingual / many
    disk_mb: int
    vram_mb: int
    ram_mb: int
    license: str
    hf_repo: str = ""
    streaming: bool = False
    word_timestamps: bool = False
    diarization: bool = False
    keyword_bias: bool = False
    default_install: bool = False
    specialist: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["profile_tags"] = list(self.profile_tags)
        d["languages"] = list(self.languages)
        return d


# Production stack + specialists (installed on demand).
MODELS: List[ModelSpec] = [
    ModelSpec(
        engine="granite",
        model_id="ibm-granite/granite-speech-4.1-2b",
        display_name="Granite Speech 4.1 2B",
        profile_tags=("quality", "auto"),
        languages=("en", "fr", "de", "es", "pt", "it", "ja", "zh"),
        disk_mb=4500,
        vram_mb=6000,
        ram_mb=8000,
        license="Apache-2.0",
        hf_repo="ibm-granite/granite-speech-4.1-2b",
        word_timestamps=False,
        keyword_bias=True,
        notes="Quality mode default; pair with forced alignment for word timing.",
    ),
    ModelSpec(
        engine="qwen3",
        model_id="Qwen/Qwen3-ASR-1.7B",
        display_name="Qwen3-ASR 1.7B",
        profile_tags=("quality", "auto"),
        languages=(),  # 52 languages
        disk_mb=3500,
        vram_mb=5000,
        ram_mb=7000,
        license="Apache-2.0",
        hf_repo="Qwen/Qwen3-ASR-1.7B",
        word_timestamps=False,
        notes="Multilingual + code-switching; Transformers backend on Windows.",
    ),
    ModelSpec(
        engine="qwen3",
        model_id="Qwen/Qwen3-ASR-0.6B",
        display_name="Qwen3-ASR 0.6B",
        profile_tags=("quality", "eco", "auto"),
        languages=(),
        disk_mb=1400,
        vram_mb=2500,
        ram_mb=4000,
        license="Apache-2.0",
        hf_repo="Qwen/Qwen3-ASR-0.6B",
        notes="Downshift when VRAM is constrained.",
    ),
    ModelSpec(
        engine="parakeet",
        model_id="nvidia/parakeet-tdt-0.6b-v3",
        display_name="Parakeet TDT 0.6B v3",
        profile_tags=("fast", "auto"),
        languages=("en", "es", "fr", "de", "it", "pt", "pl", "ru", "uk", "nl",
                   "cs", "ro", "hu", "sk", "bg", "hr", "sv", "da", "fi", "el",
                   "lt", "lv", "et", "mt", "sl"),
        disk_mb=1200,
        vram_mb=2000,
        ram_mb=4000,
        license="CC-BY-4.0",
        hf_repo="nvidia/parakeet-tdt-0.6b-v3",
        word_timestamps=True,
        notes="Fast batch with native timestamps.",
    ),
    ModelSpec(
        engine="moonshine",
        model_id="usefulsensors/moonshine-streaming-medium",
        display_name="Moonshine Streaming Medium",
        profile_tags=("live", "eco"),
        languages=("en",),
        disk_mb=400,
        vram_mb=0,
        ram_mb=1500,
        license="MIT",
        streaming=True,
        word_timestamps=True,
        notes="Native Windows/CPU streaming for live mode.",
    ),
    ModelSpec(
        engine="moonshine",
        model_id="usefulsensors/moonshine-streaming-small",
        display_name="Moonshine Streaming Small",
        profile_tags=("live", "eco"),
        languages=("en",),
        disk_mb=200,
        vram_mb=0,
        ram_mb=800,
        license="MIT",
        streaming=True,
        default_install=False,
    ),
    ModelSpec(
        engine="faster-whisper",
        model_id="large-v3-turbo",
        display_name="faster-whisper Large-v3 Turbo",
        profile_tags=("auto", "fast", "quality"),
        languages=(),
        disk_mb=1600,
        vram_mb=3000,
        ram_mb=4000,
        license="MIT",
        word_timestamps=True,
        default_install=True,
        notes="Universal fallback; mature on Windows CPU/CUDA.",
    ),
    ModelSpec(
        engine="faster-whisper",
        model_id="large-v3",
        display_name="faster-whisper Large-v3",
        profile_tags=("quality",),
        languages=(),
        disk_mb=3000,
        vram_mb=5000,
        ram_mb=6000,
        license="MIT",
        word_timestamps=True,
        default_install=True,
    ),
    ModelSpec(
        engine="faster-whisper",
        model_id="small",
        display_name="faster-whisper Small",
        profile_tags=("eco", "auto", "fast"),
        languages=(),
        disk_mb=500,
        vram_mb=1500,
        ram_mb=2000,
        license="MIT",
        word_timestamps=True,
        default_install=True,
    ),
    ModelSpec(
        engine="whisper",
        model_id="large-v3",
        display_name="OpenAI Whisper Large-v3",
        profile_tags=("quality",),
        languages=(),
        disk_mb=3000,
        vram_mb=6000,
        ram_mb=8000,
        license="MIT",
        word_timestamps=True,
        notes="Legacy optional engine.",
    ),
    ModelSpec(
        engine="firered",
        model_id="FireRedTeam/FireRedASR2-AED",
        display_name="FireRedASR2-AED",
        profile_tags=("specialist",),
        languages=("zh", "yue", "wuu"),
        disk_mb=2000,
        vram_mb=4000,
        ram_mb=6000,
        license="Apache-2.0",
        specialist=True,
        notes="Mandarin/dialect specialist; not in default install.",
    ),
    ModelSpec(
        engine="omnilingual",
        model_id="facebook/omnilingual-asr",
        display_name="Meta Omnilingual ASR",
        profile_tags=("specialist",),
        languages=(),
        disk_mb=5000,
        vram_mb=8000,
        ram_mb=10000,
        license="CC-BY-NC-4.0",
        specialist=True,
        notes="Long-tail language pack; not in default install.",
    ),
    ModelSpec(
        engine="pyannote",
        model_id="pyannote/speaker-diarization-community-1",
        display_name="pyannote Community-1",
        profile_tags=("speakers",),
        languages=(),
        disk_mb=500,
        vram_mb=1500,
        ram_mb=3000,
        license="MIT (HF gated)",
        hf_repo="pyannote/speaker-diarization-community-1",
        diarization=True,
        notes="Download once after license acceptance; then fully offline.",
    ),
]


def list_models(*, include_specialist: bool = True) -> List[ModelSpec]:
    if include_specialist:
        return list(MODELS)
    return [m for m in MODELS if not m.specialist]


def get_model(engine: str, model_id: str) -> Optional[ModelSpec]:
    for m in MODELS:
        if m.engine == engine and m.model_id == model_id:
            return m
    return None


def models_for_profile(profile: str) -> List[ModelSpec]:
    p = (profile or "auto").lower()
    return [m for m in MODELS if p in m.profile_tags or p == "auto" and "auto" in m.profile_tags]


def models_for_language(lang: str) -> List[ModelSpec]:
    code = (lang or "").lower().split("-")[0]
    if not code or code == "auto":
        return list(MODELS)
    out = []
    for m in MODELS:
        if not m.languages:  # multilingual
            out.append(m)
        elif code in m.languages:
            out.append(m)
    return out


def registry_summary() -> Dict[str, Any]:
    return {
        "models": [m.to_dict() for m in MODELS],
        "default_engines": sorted({m.engine for m in MODELS if m.default_install}),
        "specialists": sorted({m.engine for m in MODELS if m.specialist}),
    }
