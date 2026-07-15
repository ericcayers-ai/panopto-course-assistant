"""Deterministic adaptive STT router with recorded routing reasons."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import registry
from .hardware import probe_hardware, resolve_device
from .types import HardwareInfo, Profile, RouteDecision, STTRequest

# Supported language set for Granite Speech 4.1 quality mode.
GRANITE_LANGS = {"en", "fr", "de", "es", "pt", "it", "ja", "zh"}
PARAKEET_LANGS = set(registry.get_model("parakeet", "nvidia/parakeet-tdt-0.6b-v3").languages  # type: ignore
                     if registry.get_model("parakeet", "nvidia/parakeet-tdt-0.6b-v3") else ())
CHINESE_FAMILY = {"zh", "yue", "wuu", "cmn"}


def _norm_lang(lang: str) -> str:
    return (lang or "auto").lower().split("-")[0].strip() or "auto"


def _engine_ready(name: str, available: Optional[Dict[str, bool]]) -> bool:
    if available is None:
        return True  # optimistic when probes not supplied
    return bool(available.get(name))


def route(
    request: STTRequest,
    *,
    hardware: Optional[HardwareInfo] = None,
    available: Optional[Dict[str, bool]] = None,
    has_usable_captions: bool = False,
) -> RouteDecision:
    """Select engine/model for a request. Always records a plain-language reason."""
    hw = hardware or probe_hardware()
    profile = (request.profile or Profile.AUTO.value).lower()
    lang = _norm_lang(request.language)
    device = resolve_device(request.device, hw)
    fallbacks: List[str] = ["faster-whisper:large-v3-turbo", "faster-whisper:small"]

    # Explicit engine override (legacy + Advanced UI).
    if request.engine and request.engine not in ("auto", ""):
        model = request.model or _default_model_for(request.engine, hw, device)
        return RouteDecision(
            engine=request.engine,
            model=model,
            profile=profile,
            reason=f"Explicit engine override: {request.engine} / {model}.",
            fallbacks=fallbacks,
        )

    if profile == Profile.AUTO.value and has_usable_captions and request.caption_first:
        return RouteDecision(
            engine="captions",
            model="panopto",
            profile=profile,
            reason="Auto reused existing Panopto captions (no local ASR needed).",
            fallbacks=fallbacks,
        )

    if profile == Profile.LIVE.value or (profile == Profile.AUTO.value and request.extras.get("live")):
        if lang not in ("auto", "en") and lang:
            return RouteDecision(
                engine="faster-whisper",
                model="small",
                profile=Profile.LIVE.value,
                reason="Live multilingual uses rolling-window faster-whisper Small.",
                fallbacks=fallbacks,
            )
        if _engine_ready("moonshine", available):
            model = "usefulsensors/moonshine-streaming-medium"
            if hw.ram_mb and hw.ram_mb < 2048:
                model = "usefulsensors/moonshine-streaming-small"
            return RouteDecision(
                engine="moonshine",
                model=model,
                profile=Profile.LIVE.value,
                reason="Live mode → Moonshine streaming on CPU/Windows.",
                fallbacks=["faster-whisper:small"] + fallbacks,
            )
        return RouteDecision(
            engine="faster-whisper",
            model="small",
            profile=Profile.LIVE.value,
            reason="Live mode → faster-whisper Small (Moonshine not installed).",
            fallbacks=fallbacks,
        )

    if profile == Profile.ECO.value or (device == "cpu" and profile == Profile.AUTO.value and hw.vram_mb == 0 and (hw.ram_mb or 0) < 8000):
        if _engine_ready("moonshine", available) and lang in ("auto", "en"):
            return RouteDecision(
                engine="moonshine",
                model="usefulsensors/moonshine-streaming-small",
                profile=Profile.ECO.value,
                reason="Eco/CPU → Moonshine Small.",
                fallbacks=["faster-whisper:small"],
            )
        return RouteDecision(
            engine="faster-whisper",
            model="small",
            profile=Profile.ECO.value if profile == Profile.ECO.value else Profile.AUTO.value,
            reason="Eco/CPU → faster-whisper Small.",
            fallbacks=fallbacks,
        )

    if profile == Profile.FAST.value:
        if lang in PARAKEET_LANGS or lang == "auto":
            if _engine_ready("parakeet", available):
                return RouteDecision(
                    engine="parakeet",
                    model="nvidia/parakeet-tdt-0.6b-v3",
                    profile=profile,
                    reason="Fast → Parakeet TDT 0.6B v3 (native timestamps).",
                    fallbacks=["faster-whisper:large-v3-turbo"] + fallbacks,
                )
        return RouteDecision(
            engine="faster-whisper",
            model="large-v3-turbo",
            profile=profile,
            reason="Fast → faster-whisper large-v3-turbo (Parakeet unavailable or language unsupported).",
            fallbacks=fallbacks,
        )

    # Specialist Chinese pack
    if lang in CHINESE_FAMILY and _engine_ready("firered", available):
        return RouteDecision(
            engine="firered",
            model="FireRedTeam/FireRedASR2-AED",
            profile=profile,
            reason="Chinese/dialect language → FireRedASR2 specialist pack.",
            fallbacks=["qwen3:Qwen/Qwen3-ASR-1.7B"] + fallbacks,
        )

    # Quality / Auto quality branch
    want_quality = profile in {Profile.QUALITY.value, Profile.AUTO.value}
    multilingual = lang == "auto" or request.code_switch or (lang not in GRANITE_LANGS and lang != "auto")
    # When language is auto, prefer Granite if we have VRAM for quality mono-ish workload,
    # else Qwen for broad multilingual; Auto still prefers Granite for quality when ready.
    if want_quality:
        if (request.code_switch or (lang not in GRANITE_LANGS and lang not in ("auto", ""))) \
                and _engine_ready("qwen3", available):
            model = "Qwen/Qwen3-ASR-1.7B"
            if hw.vram_mb and hw.vram_mb < 4500:
                model = "Qwen/Qwen3-ASR-0.6B"
            return RouteDecision(
                engine="qwen3",
                model=model,
                profile=Profile.QUALITY.value if profile == Profile.QUALITY.value else Profile.AUTO.value,
                reason="Multilingual/code-switch → Qwen3-ASR.",
                fallbacks=["faster-whisper:large-v3-turbo"] + fallbacks,
            )
        if lang in GRANITE_LANGS or lang == "auto":
            if _engine_ready("granite", available) and (device == "cuda" and (hw.vram_mb == 0 or hw.vram_mb >= 5000) or device == "cpu" and (hw.ram_mb or 0) >= 10000):
                return RouteDecision(
                    engine="granite",
                    model="ibm-granite/granite-speech-4.1-2b",
                    profile=Profile.QUALITY.value if profile == Profile.QUALITY.value else Profile.AUTO.value,
                    reason="Quality supported-language → Granite Speech 4.1 2B.",
                    fallbacks=["qwen3:Qwen/Qwen3-ASR-1.7B", "faster-whisper:large-v3"] + fallbacks,
                )
            if _engine_ready("qwen3", available) and profile == Profile.QUALITY.value:
                model = "Qwen/Qwen3-ASR-1.7B" if (not hw.vram_mb or hw.vram_mb >= 4500) else "Qwen/Qwen3-ASR-0.6B"
                return RouteDecision(
                    engine="qwen3",
                    model=model,
                    profile=Profile.QUALITY.value,
                    reason="Quality → Qwen3-ASR (Granite not ready on this hardware).",
                    fallbacks=["faster-whisper:large-v3"] + fallbacks,
                )

    # Universal fallback — calibrate model to VRAM like the old recommend_settings.
    fw_model = _faster_whisper_model_for(hw, device, prefer_turbo=profile != Profile.QUALITY.value)
    return RouteDecision(
        engine="faster-whisper",
        model=fw_model,
        profile=profile,
        reason=f"Universal fallback → faster-whisper {fw_model} "
               f"({'CUDA' if device == 'cuda' else 'CPU'}).",
        fallbacks=["faster-whisper:small"] if fw_model != "small" else [],
    )


def _faster_whisper_model_for(hw: HardwareInfo, device: str, prefer_turbo: bool = True) -> str:
    if device != "cuda":
        return "small"
    vram = hw.vram_mb or 0
    if vram >= 10000:
        return "large-v3-turbo" if prefer_turbo else "large-v3"
    if vram >= 6000:
        return "large-v3-turbo" if prefer_turbo else "large-v3"
    if vram >= 4000:
        return "large-v3-turbo" if prefer_turbo else "medium"
    if vram > 0:
        return "small"
    # CUDA present but VRAM unknown — prefer turbo over inventing 4 GB.
    return "large-v3-turbo" if prefer_turbo else "medium"


def _default_model_for(engine: str, hw: HardwareInfo, device: str) -> str:
    if engine == "faster-whisper":
        return _faster_whisper_model_for(hw, device)
    if engine == "whisper":
        return "small" if device == "cpu" else "large-v3"
    if engine == "granite":
        return "ibm-granite/granite-speech-4.1-2b"
    if engine == "qwen3":
        return "Qwen/Qwen3-ASR-0.6B" if hw.vram_mb and hw.vram_mb < 4500 else "Qwen/Qwen3-ASR-1.7B"
    if engine == "parakeet":
        return "nvidia/parakeet-tdt-0.6b-v3"
    if engine == "moonshine":
        return "usefulsensors/moonshine-streaming-medium"
    if engine == "firered":
        return "FireRedTeam/FireRedASR2-AED"
    if engine == "omnilingual":
        return "facebook/omnilingual-asr"
    return "small"


def next_fallback(decision: RouteDecision, failed: Optional[str] = None) -> Optional[RouteDecision]:
    """Return the next fallback as a RouteDecision, or None."""
    remaining = list(decision.fallbacks)
    if failed:
        token = f"{decision.engine}:{decision.model}"
        remaining = [f for f in remaining if f != token and f != failed]
    if not remaining:
        return None
    token = remaining[0]
    engine, _, model = token.partition(":")
    return RouteDecision(
        engine=engine,
        model=model or "small",
        profile=decision.profile,
        reason=f"Fallback after failure → {engine}/{model or 'small'}.",
        fallbacks=remaining[1:],
    )


def explain_route(decision: RouteDecision) -> Dict[str, Any]:
    return decision.to_dict()
