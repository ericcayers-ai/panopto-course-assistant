"""
tts.py - VibeVoice text-to-speech integration.

Setup is a single command (see requirements-tts.txt):

    pip install -r requirements-tts.txt

That installs the VibeVoice package (Realtime-0.5B streaming TTS) straight from
GitHub. The ~1 GB model is fetched from Hugging Face on first generation, and
the small voice-preset files (2-7 MB each) are downloaded on demand from GitHub
into OUTPUT_DIR/_tts_voices/ - so there is no need to clone the repo by hand.
"""
from __future__ import annotations

import glob
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# --- voice catalog ------------------------------------------------------------

_LANG_MAP = {
    "en": "English", "de": "German", "fr": "French", "it": "Italian",
    "jp": "Japanese", "kr": "Korean", "nl": "Dutch", "pl": "Polish",
    "pt": "Portuguese", "sp": "Spanish", "in": "Hindi",
}

# The voice presets bundled with VibeVoice-Realtime-0.5B. These are NOT shipped
# in the pip package (only the repo's demo/ folder), so we download them on
# demand from the raw GitHub URLs below. English first (best supported).
KNOWN_VOICES: List[str] = [
    "en-Carter_man", "en-Davis_man", "en-Emma_woman", "en-Frank_man",
    "en-Grace_woman", "en-Mike_man",
    "de-Spk0_man", "de-Spk1_woman", "fr-Spk0_man", "fr-Spk1_woman",
    "in-Samuel_man", "it-Spk0_woman", "it-Spk1_man", "jp-Spk0_man",
    "jp-Spk1_woman", "kr-Spk0_woman", "kr-Spk1_man", "nl-Spk0_man",
    "nl-Spk1_woman", "pl-Spk0_man", "pl-Spk1_woman", "pt-Spk0_woman",
    "pt-Spk1_man", "sp-Spk0_woman", "sp-Spk1_man",
]

_VOICE_BASE_URL = (
    "https://raw.githubusercontent.com/microsoft/VibeVoice/main/"
    "demo/voices/streaming_model/"
)


def _voice_label(stem: str) -> str:
    """'en-Carter_man' -> 'Carter (English, Male)'"""
    parts = stem.split("-", 1)
    if len(parts) != 2:
        return stem
    lang, rest = parts
    lang_label = _LANG_MAP.get(lang.lower(), lang.upper())
    sub = rest.split("_")
    speaker = sub[0]
    gender = {"man": "Male", "woman": "Female"}.get(
        (sub[1].lower() if len(sub) > 1 else ""), "")
    return f"{speaker} ({lang_label}{', ' + gender if gender else ''})"


def _repo_voices_dir() -> Optional[Path]:
    """Locate voice .pt files inside an editable VibeVoice clone, if present.

    Honours VIBEVOICE_VOICES_DIR, otherwise looks next to the installed package
    (works for `pip install -e .`). Returns None when the repo's demo/ folder is
    not on disk (the normal case for a plain pip install).
    """
    env = os.environ.get("VIBEVOICE_VOICES_DIR")
    if env and Path(env).exists():
        return Path(env)
    try:
        import vibevoice as _vv
        candidate = Path(_vv.__file__).resolve().parent.parent / \
            "demo" / "voices" / "streaming_model"
        if candidate.exists():
            return candidate
    except ImportError:
        pass
    return None


def list_voices(cache_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return [{id, label, downloaded}] for every known voice.

    The full catalog is always returned (so the UI is populated immediately);
    `downloaded` reflects whether the preset is already cached locally.
    """
    repo_dir = _repo_voices_dir()
    have: set[str] = set()
    if repo_dir:
        have |= {Path(p).stem for p in glob.glob(str(repo_dir / "*.pt"))}
    if cache_dir and cache_dir.exists():
        have |= {Path(p).stem for p in glob.glob(str(cache_dir / "*.pt"))}

    out = []
    for vid in KNOWN_VOICES:
        out.append({"id": vid, "label": _voice_label(vid),
                    "downloaded": vid in have})
    # Surface any extra voices found locally but not in the static catalog.
    for vid in sorted(have - set(KNOWN_VOICES)):
        out.append({"id": vid, "label": _voice_label(vid), "downloaded": True})
    return out


def ensure_voice(voice_id: str, cache_dir: Path,
                 progress: Optional[Callable[[str, float], None]] = None) -> Path:
    """Return a local path to the voice preset, downloading it if necessary.

    Prefers a cloned-repo copy; otherwise downloads from GitHub into cache_dir.
    """
    # 1) Already in a cloned repo?
    repo_dir = _repo_voices_dir()
    if repo_dir:
        p = repo_dir / f"{voice_id}.pt"
        if p.exists():
            return p
    # 2) Already cached?
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{voice_id}.pt"
    if cached.exists() and cached.stat().st_size > 0:
        return cached
    # 3) Download from GitHub.
    if voice_id not in KNOWN_VOICES:
        raise ValueError(f"Unknown voice {voice_id!r}.")
    if progress:
        progress(f"Downloading voice '{voice_id}'", 0.05)
    url = _VOICE_BASE_URL + f"{voice_id}.pt"
    tmp = cached.with_suffix(".pt.part")
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 (trusted GitHub host)
        tmp.replace(cached)
    except Exception as exc:  # pragma: no cover - network failure path
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not download voice '{voice_id}' from GitHub: {exc}") from exc
    return cached


def is_available() -> bool:
    """True when the vibevoice package can be imported."""
    try:
        import vibevoice  # noqa: F401
        return True
    except ImportError:
        return False


# --- markdown -> plain text ---------------------------------------------------

def strip_markdown(text: str) -> str:
    """Remove markdown syntax so the TTS model only sees prose."""
    text = re.sub(r"```[\s\S]*?```", "", text)        # fenced code
    text = re.sub(r"`[^`]+`", "", text)               # inline code
    text = re.sub(r"<[^>]+>", "", text)               # HTML tags
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)   # headings
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)        # bold/italic
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)         # links
    text = re.sub(r"!\[[^\]]*\]\([^\)]+\)", "", text)            # images
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)  # hr
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)    # bullets
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)    # numbered
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)        # quotes
    text = re.sub(r"\|", " ", text)                                  # table pipes
    text = re.sub(r"^[-:\s]+$", "", text, flags=re.MULTILINE)        # table sep
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- generation ---------------------------------------------------------------

MODEL_ID = "microsoft/VibeVoice-Realtime-0.5B"


def generate(
    md_path: str,
    voice_id: str,
    output_path: str,
    cache_dir: Path,
    progress: Callable[[str, float], None],
    model_path: str = MODEL_ID,
) -> Dict[str, Any]:
    """Convert a markdown file to speech and write a WAV file.

    progress(stage_label, fraction_0_to_1) is called throughout.
    Returns {"output_path", "voice", "duration_s"}.
    """
    import copy
    import torch

    from vibevoice.modular.modeling_vibevoice_streaming_inference import (
        VibeVoiceStreamingForConditionalGenerationInference,
    )
    from vibevoice.processor.vibevoice_streaming_processor import (
        VibeVoiceStreamingProcessor,
    )
    from transformers.cache_utils import DynamicCache
    from transformers.modeling_outputs import BaseModelOutputWithPast

    # -- Read + clean text ------------------------------------------------
    progress("Reading file", 0.02)
    text = Path(md_path).read_text(encoding="utf-8", errors="replace")
    text = strip_markdown(text)
    if not text:
        raise ValueError("No readable text found after stripping markdown.")

    # -- Resolve voice preset (downloads on demand) -----------------------
    voice_path = ensure_voice(voice_id, cache_dir, progress)

    # -- Device / dtype ---------------------------------------------------
    if torch.cuda.is_available():
        device, dtype, attn = "cuda", torch.bfloat16, "flash_attention_2"
    elif torch.backends.mps.is_available():
        device, dtype, attn = "mps", torch.float32, "sdpa"
    else:
        device, dtype, attn = "cpu", torch.float32, "sdpa"

    # -- Load processor + model -------------------------------------------
    progress(f"Loading processor ({device})", 0.10)
    processor = VibeVoiceStreamingProcessor.from_pretrained(model_path)

    progress(f"Loading model ({device})", 0.18)
    try:
        if device == "mps":
            model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                model_path, torch_dtype=dtype, attn_implementation=attn, device_map=None)
            model.to("mps")
        else:
            model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                model_path, torch_dtype=dtype, device_map=device, attn_implementation=attn)
    except Exception:
        # Flash attention not installed -> fall back to SDPA.
        model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            model_path, torch_dtype=dtype, attn_implementation="sdpa",
            device_map=device if device in ("cuda", "cpu") else None)
        if device == "mps":
            model.to("mps")

    model.eval()
    model.set_ddpm_inference_steps(num_steps=5)

    # -- Load voice preset ------------------------------------------------
    # The presets store rich objects (BaseModelOutputWithPast / DynamicCache),
    # not a plain state dict. PyTorch 2.6+ defaults to weights_only=True, whose
    # unpickler rejects these even when allowlisted (they are used as dicts).
    # These files are downloaded by us from Microsoft's official GitHub, so we
    # load with weights_only=False; the allowlisted path is tried first.
    progress("Loading voice preset", 0.28)
    try:
        with torch.serialization.safe_globals([BaseModelOutputWithPast, DynamicCache]):
            prefilled = torch.load(str(voice_path), map_location=device, weights_only=True)
    except Exception:
        prefilled = torch.load(str(voice_path), map_location=device, weights_only=False)

    # -- Prepare inputs ---------------------------------------------------
    progress("Processing text", 0.36)
    inputs = processor.process_input_with_cached_prompt(
        text=text, cached_prompt=prefilled, padding=True,
        return_tensors="pt", return_attention_mask=True)
    for k, v in inputs.items():
        if torch.is_tensor(v):
            inputs[k] = v.to(device)

    # -- Generate ---------------------------------------------------------
    progress("Generating speech (may take several minutes on CPU)", 0.42)
    outputs = model.generate(
        **inputs, max_new_tokens=None, cfg_scale=1.5,
        tokenizer=processor.tokenizer, generation_config={"do_sample": False},
        verbose=False,
        all_prefilled_outputs=copy.deepcopy(prefilled))

    if not outputs.speech_outputs or outputs.speech_outputs[0] is None:
        raise RuntimeError("VibeVoice returned no audio output.")

    # -- Save WAV ---------------------------------------------------------
    progress("Saving audio", 0.92)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    processor.save_audio(outputs.speech_outputs[0], output_path=output_path)

    n_samples = outputs.speech_outputs[0].shape[-1]
    duration = n_samples / 24000  # VibeVoice outputs 24 kHz audio

    progress("done", 1.0)
    return {"output_path": output_path, "voice": voice_id,
            "duration_s": round(duration, 1)}
