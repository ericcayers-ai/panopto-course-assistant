"""
tts.py - Kokoro-82M text-to-speech integration.

Setup:

    pip install -r requirements-tts.txt

That installs the ``kokoro`` package (Apache-2.0, ~82M params) plus English G2P
(``misaki[en]``) and ``soundfile``. The model (~300 MB) downloads from Hugging
Face on first generation. Voices are bundled with the package - no separate
voice downloads.

Designed for long-form lecture / Markdown narration: text is chunked into
sentence-bounded segments in Kokoro's preferred length range, synthesised with
the same speaker settings, then concatenated into one WAV with short pauses.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# --- voice catalog ------------------------------------------------------------

# Highest-graded English voices first (see hexgrad/Kokoro-82M VOICES.md).
# id pattern: {lang}{gender}_{name}  e.g. af_heart, bm_george
KNOWN_VOICES: List[Dict[str, str]] = [
    # American English (lang_code='a')
    {"id": "af_heart", "label": "Heart (American English, Female)", "lang": "a", "group": "American English"},
    {"id": "af_bella", "label": "Bella (American English, Female)", "lang": "a", "group": "American English"},
    {"id": "af_nicole", "label": "Nicole (American English, Female)", "lang": "a", "group": "American English"},
    {"id": "af_sarah", "label": "Sarah (American English, Female)", "lang": "a", "group": "American English"},
    {"id": "af_sky", "label": "Sky (American English, Female)", "lang": "a", "group": "American English"},
    {"id": "am_michael", "label": "Michael (American English, Male)", "lang": "a", "group": "American English"},
    {"id": "am_fenrir", "label": "Fenrir (American English, Male)", "lang": "a", "group": "American English"},
    {"id": "am_puck", "label": "Puck (American English, Male)", "lang": "a", "group": "American English"},
    {"id": "am_adam", "label": "Adam (American English, Male)", "lang": "a", "group": "American English"},
    # British English (lang_code='b')
    {"id": "bf_emma", "label": "Emma (British English, Female)", "lang": "b", "group": "British English"},
    {"id": "bf_isabella", "label": "Isabella (British English, Female)", "lang": "b", "group": "British English"},
    {"id": "bm_george", "label": "George (British English, Male)", "lang": "b", "group": "British English"},
    {"id": "bm_fable", "label": "Fable (British English, Male)", "lang": "b", "group": "British English"},
    {"id": "bm_lewis", "label": "Lewis (British English, Male)", "lang": "b", "group": "British English"},
    # Other languages (best-effort; may need espeak-ng / misaki extras)
    {"id": "ef_dora", "label": "Dora (Spanish, Female)", "lang": "e", "group": "Other languages"},
    {"id": "em_alex", "label": "Alex (Spanish, Male)", "lang": "e", "group": "Other languages"},
    {"id": "ff_siwis", "label": "Siwis (French, Female)", "lang": "f", "group": "Other languages"},
    {"id": "if_sara", "label": "Sara (Italian, Female)", "lang": "i", "group": "Other languages"},
    {"id": "im_nicola", "label": "Nicola (Italian, Male)", "lang": "i", "group": "Other languages"},
    {"id": "hf_alpha", "label": "Alpha (Hindi, Female)", "lang": "h", "group": "Other languages"},
    {"id": "hm_omega", "label": "Omega (Hindi, Male)", "lang": "h", "group": "Other languages"},
    {"id": "pf_dora", "label": "Dora (Brazilian Portuguese, Female)", "lang": "p", "group": "Other languages"},
    {"id": "pm_alex", "label": "Alex (Brazilian Portuguese, Male)", "lang": "p", "group": "Other languages"},
]

_VOICE_BY_ID = {v["id"]: v for v in KNOWN_VOICES}

# Kokoro docs: goldilocks ~100–200 tokens; rushing begins past ~400.
# Character budget ≈ tokens for English narration prose.
DEFAULT_CHUNK_CHARS = 220
MAX_CHUNK_CHARS = 380
SAMPLE_RATE = 24000
CHUNK_PAUSE_S = 0.22  # silence between chunks for natural paragraph breaks

MODEL_ID = "hexgrad/Kokoro-82M"


def list_voices(cache_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the voice catalog. Kokoro ships voices with the package."""
    del cache_dir  # reserved for API compatibility with the Speech tab
    return [
        {
            "id": v["id"],
            "label": v["label"],
            "lang": v["lang"],
            "group": v["group"],
            "downloaded": True,
        }
        for v in KNOWN_VOICES
    ]


def _lang_for_voice(voice_id: str) -> str:
    meta = _VOICE_BY_ID.get(voice_id)
    if meta:
        return meta["lang"]
    # Fall back to first letter of Kokoro voice ids (af_ / bm_ / …).
    if voice_id and voice_id[0].isalpha():
        return voice_id[0].lower()
    return "a"


def is_available() -> bool:
    """True when the kokoro package can be imported."""
    try:
        import kokoro  # noqa: F401
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
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- long-form chunking -------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\u2026])\s+(?=[A-Z0-9\"'(])")


def _split_sentences(paragraph: str) -> List[str]:
    paragraph = " ".join(paragraph.split())
    if not paragraph:
        return []
    parts = _SENTENCE_SPLIT.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> List[str]:
    """Split prose into synthesis-sized chunks that prefer sentence boundaries.

    Kokoro rushes or loses quality when a single utterance exceeds ~400 tokens.
    Packing ~150–220 character chunks keeps prosody stable across long lectures.
    """
    max_chars = max(40, min(int(max_chars), MAX_CHUNK_CHARS))
    chunks: List[str] = []

    paragraphs = re.split(r"\n\s*\n+", text)
    for para in paragraphs:
        sentences = _split_sentences(para)
        if not sentences:
            continue
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                # Hard-wrap overlong sentences on commas / spaces.
                pieces = re.split(r"(?<=[,;:])\s+|\s+", sentence)
                buf = ""
                for piece in pieces:
                    if not piece:
                        continue
                    candidate = f"{buf} {piece}".strip() if buf else piece
                    if len(candidate) <= max_chars:
                        buf = candidate
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = piece if len(piece) <= max_chars else piece[:max_chars]
                if buf:
                    current = buf
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current.strip())

    return [c for c in chunks if c]


def _concat_audio(segments: List[Any], sample_rate: int = SAMPLE_RATE,
                  pause_s: float = CHUNK_PAUSE_S) -> Any:
    """Concatenate float audio arrays with a short silence between chunks."""
    import numpy as np

    if not segments:
        return np.zeros(0, dtype=np.float32)

    pause = np.zeros(max(0, int(sample_rate * pause_s)), dtype=np.float32)
    parts: List[Any] = []
    for i, seg in enumerate(segments):
        arr = np.asarray(seg, dtype=np.float32).reshape(-1)
        parts.append(arr)
        if i < len(segments) - 1 and pause.size:
            parts.append(pause)
    return np.concatenate(parts)


# --- generation ---------------------------------------------------------------

def generate(
    md_path: str,
    voice_id: str,
    output_path: str,
    cache_dir: Path,
    progress: Callable[[str, float], None],
    model_path: str = MODEL_ID,
    speed: float = 1.0,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> Dict[str, Any]:
    """Convert a markdown file to speech and write a WAV file.

    progress(stage_label, fraction_0_to_1) is called throughout.
    Returns {"output_path", "voice", "duration_s", "chunks"}.
    """
    del cache_dir  # voices ship with kokoro; kept for call-site compatibility

    progress("Reading file", 0.02)
    text = Path(md_path).read_text(encoding="utf-8", errors="replace")
    text = strip_markdown(text)
    if not text:
        raise ValueError("No readable text found after stripping markdown.")

    if voice_id not in _VOICE_BY_ID and not re.match(r"^[a-z]{1,2}_[a-z0-9]+$", voice_id):
        raise ValueError(
            f"Unknown voice {voice_id!r}. Choose one of: "
            + ", ".join(v["id"] for v in KNOWN_VOICES[:8])
            + ", …"
        )

    chunks = chunk_text(text, max_chars=chunk_chars)
    if not chunks:
        raise ValueError("No synthesizable chunks after splitting text.")

    import numpy as np
    import soundfile as sf
    import torch
    from kokoro import KPipeline

    lang = _lang_for_voice(voice_id)
    if torch.cuda.is_available():
        device = "cuda"
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    progress(f"Loading Kokoro ({device}, {lang})", 0.08)
    # repo_id lets advanced users point at a local checkout or mirror.
    try:
        pipeline = KPipeline(lang_code=lang, repo_id=model_path or MODEL_ID, device=device)
    except TypeError:
        # Older kokoro builds omit device/repo_id kwargs.
        try:
            pipeline = KPipeline(lang_code=lang, repo_id=model_path or MODEL_ID)
        except TypeError:
            pipeline = KPipeline(lang_code=lang)

    audio_parts: List[Any] = []
    n = len(chunks)
    # Generation occupies most of the progress bar (0.12 → 0.90).
    for i, chunk in enumerate(chunks):
        frac = 0.12 + 0.78 * (i / max(n, 1))
        preview = chunk if len(chunk) <= 64 else chunk[:61] + "…"
        progress(f"Speaking chunk {i + 1}/{n}: {preview}", frac)

        # Disable internal re-splitting so our chunk sizes stay intact.
        try:
            gen = pipeline(chunk, voice=voice_id, speed=speed, split_pattern=None)
        except TypeError:
            gen = pipeline(chunk, voice=voice_id, speed=speed)

        chunk_audio: List[Any] = []
        for _gs, _ps, audio in gen:
            if audio is None:
                continue
            chunk_audio.append(np.asarray(audio, dtype=np.float32).reshape(-1))
        if not chunk_audio:
            raise RuntimeError(f"Kokoro returned no audio for chunk {i + 1}/{n}.")
        audio_parts.append(np.concatenate(chunk_audio) if len(chunk_audio) > 1 else chunk_audio[0])

    progress("Concatenating audio", 0.92)
    combined = _concat_audio(audio_parts, SAMPLE_RATE, CHUNK_PAUSE_S)
    if combined.size == 0:
        raise RuntimeError("Kokoro produced empty audio.")

    # Soft peak normalize to avoid rare clip spikes across long concatenations.
    peak = float(np.max(np.abs(combined))) if combined.size else 0.0
    if peak > 1.0:
        combined = combined / peak * 0.99

    progress("Saving WAV", 0.96)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), combined, SAMPLE_RATE)

    duration = float(combined.shape[-1]) / SAMPLE_RATE
    progress("done", 1.0)
    return {
        "output_path": str(out),
        "voice": voice_id,
        "duration_s": round(duration, 1),
        "chunks": n,
        "sample_rate": SAMPLE_RATE,
        "model": model_path or MODEL_ID,
    }
