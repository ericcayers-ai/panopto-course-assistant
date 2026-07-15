"""Forced alignment, diarization, vocabulary, and correction enrichment."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..types import DiarizationSource, Segment, SpeakerTurn, STTResult, TimingSource, Word


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def force_align(
    result: STTResult,
    audio_path: str,
    *,
    language: str = "en",
) -> STTResult:
    """Run Qwen3 ForcedAligner 0.6B when available; otherwise leave timings as-is."""
    if result.timing_source in {TimingSource.NATIVE.value, TimingSource.CAPTION.value}:
        if any(s.words for s in result.segments):
            return result
    if not (_have("transformers") and _have("torch")):
        return result
    result.metrics = dict(result.metrics or {})
    result.metrics["align_attempted"] = True
    result.raw_provenance = dict(result.raw_provenance or {})
    try:
        import torch
        from transformers import AutoModel, AutoProcessor
    except Exception as e:
        result.metrics["align_applied"] = False
        result.raw_provenance["aligner"] = f"qwen3-forced-aligner-unavailable:{e}"
        return result

    model_id = "Qwen/Qwen3-ForcedAligner-0.6B"
    try:
        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        model = AutoModel.from_pretrained(model_id, trust_remote_code=True)
        model.eval()
        # Best-effort API: models differ; treat failure as soft skip.
        audio = Path(audio_path)
        if not audio.is_file():
            raise FileNotFoundError(audio_path)
        text = result.text or " ".join(s.text for s in result.segments if s.text)
        if not text.strip():
            raise ValueError("empty transcript")
        inputs = processor(audio=str(audio), text=text, return_tensors="pt")
        with torch.no_grad():
            out = model(**{k: v for k, v in inputs.items() if hasattr(v, "to")})
        # Prefer processor post-process when available.
        words_out: List[Word] = []
        if hasattr(processor, "decode_alignment"):
            aligned = processor.decode_alignment(out)
            for w in aligned or []:
                words_out.append(Word(
                    word=str(w.get("word") or w.get("text") or ""),
                    start=float(w.get("start") or 0.0),
                    end=float(w.get("end") or 0.0),
                    confidence=_opt_conf(w.get("confidence")),
                ))
        elif isinstance(out, dict) and out.get("words"):
            for w in out["words"]:
                words_out.append(Word(
                    word=str(w.get("word") or ""),
                    start=float(w.get("start") or 0.0),
                    end=float(w.get("end") or 0.0),
                    confidence=_opt_conf(w.get("confidence")),
                ))
        if not words_out:
            raise RuntimeError("aligner returned no words")
        result.segments = _words_to_segments(words_out, result.segments)
        result.timing_source = TimingSource.FORCED_ALIGN.value
        result.metrics["align_applied"] = True
        result.raw_provenance["aligner"] = model_id
    except Exception as e:
        result.metrics["align_applied"] = False
        result.raw_provenance["aligner"] = f"qwen3-forced-aligner-0.6b-skipped:{e}"
        if not result.timing_source:
            result.timing_source = TimingSource.NONE.value
    return result


def _opt_conf(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _words_to_segments(words: Sequence[Word], existing: Sequence[Segment]) -> List[Segment]:
    """Distribute aligned words into existing segment windows when possible."""
    if not words:
        return list(existing)
    if not existing:
        text = " ".join(w.word for w in words).strip()
        return [Segment(
            id=1, start=words[0].start, end=words[-1].end, text=text, words=list(words),
        )]
    out: List[Segment] = []
    wi = 0
    for seg in existing:
        bucket: List[Word] = []
        while wi < len(words):
            w = words[wi]
            mid = (w.start + w.end) / 2.0
            if mid < seg.start - 0.05:
                wi += 1
                continue
            if mid > seg.end + 0.25 and bucket:
                break
            if mid > seg.end + 0.25 and not bucket:
                break
            bucket.append(w)
            wi += 1
        text = " ".join(w.word for w in bucket).strip() or seg.text
        out.append(Segment(
            id=seg.id, start=seg.start, end=seg.end, text=text,
            speaker=seg.speaker, language=seg.language,
            confidence=seg.confidence, words=bucket,
        ))
    return out


def diarize(
    result: STTResult,
    audio_path: str,
    *,
    mode: str = "auto",
    num_speakers: Optional[int] = None,
    hf_token: str = "",
) -> STTResult:
    """Offline pyannote Community-1 diarization when installed + licensed."""
    mode = (mode or "off").lower()
    if mode in {"off", "false", "0", "no"}:
        return result
    if not _have("pyannote"):
        result.metrics = dict(result.metrics or {})
        result.metrics["diarization_skipped"] = "pyannote_not_installed"
        return result
    try:
        from pyannote.audio import Pipeline
        kwargs = {}
        if hf_token:
            kwargs["use_auth_token"] = hf_token
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-community-1", **kwargs
        )
        diarization = pipeline(audio_path, num_speakers=num_speakers)
    except Exception as e:
        result.metrics = dict(result.metrics or {})
        result.metrics["diarization_error"] = str(e)[:300]
        return result

    turns: List[SpeakerTurn] = []
    speaker_map: Dict[str, str] = {}
    next_id = 0
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        if speaker not in speaker_map:
            speaker_map[speaker] = f"SPEAKER_{next_id:02d}"
            next_id += 1
        label = speaker_map[speaker]
        turns.append(SpeakerTurn(speaker=label, start=float(turn.start), end=float(turn.end)))

    result.speakers = turns
    result.diarization_source = DiarizationSource.PYANNOTE.value
    result.segments = reconcile_speakers(result.segments, turns)
    return result


def reconcile_speakers(segments: Sequence[Segment], turns: Sequence[SpeakerTurn]) -> List[Segment]:
    """Assign SPEAKER_XX labels to segments (and words) via mid-point overlap."""
    out: List[Segment] = []
    for seg in segments:
        mid = (seg.start + seg.end) / 2.0
        label = _speaker_at(mid, turns)
        words = []
        for w in seg.words:
            w_mid = (w.start + w.end) / 2.0
            words.append(Word(
                word=w.word, start=w.start, end=w.end, confidence=w.confidence,
                speaker=_speaker_at(w_mid, turns) or label,
            ))
        out.append(Segment(
            id=seg.id, start=seg.start, end=seg.end, text=seg.text,
            speaker=label, language=seg.language, confidence=seg.confidence, words=words,
        ))
    return out


def _speaker_at(t: float, turns: Sequence[SpeakerTurn]) -> Optional[str]:
    for turn in turns:
        if turn.start <= t <= turn.end:
            return turn.speaker
    # nearest
    best = None
    best_dist = 1e9
    for turn in turns:
        dist = min(abs(t - turn.start), abs(t - turn.end))
        if dist < best_dist:
            best_dist, best = dist, turn.speaker
    return best if best_dist < 2.0 else None


def detect_chunk_language(text: str, fallback: str = "") -> str:
    """Lightweight heuristic language ID per chunk (no cloud)."""
    if not text or not text.strip():
        return fallback or ""
    # CJK
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    if re.search(r"[\u0600-\u06ff]", text):
        return "ar"
    return fallback or "en"


def apply_per_segment_language(result: STTResult, default: str = "") -> STTResult:
    for seg in result.segments:
        if not seg.language:
            seg.language = detect_chunk_language(seg.text, default or result.language)
    return result


def build_course_vocabulary(
    *,
    titles: Sequence[str] = (),
    outline_text: str = "",
    glossary_terms: Sequence[str] = (),
    lecturer_names: Sequence[str] = (),
    headings: Sequence[str] = (),
    user_corrections: Sequence[str] = (),
    limit: int = 128,
) -> List[str]:
    """Assemble domain vocabulary for keyword bias / hotwords / prompts."""
    seen = set()
    out: List[str] = []

    def add(term: str) -> None:
        t = re.sub(r"\s+", " ", (term or "").strip())
        if len(t) < 2 or len(t) > 64:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    for src in (user_corrections, glossary_terms, lecturer_names, titles, headings):
        for term in src:
            add(term)
            if len(out) >= limit:
                return out
    for line in (outline_text or "").splitlines():
        line = line.strip().lstrip("#*- ").strip()
        if 2 <= len(line) <= 64:
            add(line)
        if len(out) >= limit:
            break
    return out


def apply_corrections(
    result: STTResult,
    mapping: Dict[str, str],
    *,
    retain_raw: bool = True,
) -> STTResult:
    """Non-destructive correction dictionary; provenance kept in corrections[]."""
    if not mapping:
        return result
    if retain_raw and "raw_text" not in (result.raw_provenance or {}):
        result.raw_provenance = dict(result.raw_provenance or {})
        result.raw_provenance["raw_text"] = result.text
        result.raw_provenance["raw_segments"] = [s.to_dict() for s in result.segments]

    corrections = list(result.corrections or [])
    new_segs: List[Segment] = []
    for seg in result.segments:
        text = seg.text
        for src, dst in mapping.items():
            if not src or src == dst:
                continue
            if src in text:
                text2 = text.replace(src, dst)
                if text2 != text:
                    corrections.append({
                        "from": src, "to": dst,
                        "segment_id": seg.id,
                        "start": seg.start, "end": seg.end,
                    })
                    text = text2
        new_segs.append(Segment(
            id=seg.id, start=seg.start, end=seg.end, text=text,
            speaker=seg.speaker, language=seg.language,
            confidence=seg.confidence, words=list(seg.words),
        ))
    result.segments = new_segs
    result.text = " ".join(s.text for s in new_segs if s.text).strip()
    result.corrections = corrections
    return result


def enrich(
    result: STTResult,
    audio_path: str,
    *,
    word_timestamps: bool = True,
    diarization_mode: str = "off",
    speakers: Optional[int] = None,
    hf_token: str = "",
    corrections: Optional[Dict[str, str]] = None,
    default_language: str = "",
) -> STTResult:
    """Run optional enrichment passes in order."""
    if word_timestamps and result.engine in {"granite", "qwen3"}:
        result = force_align(result, audio_path, language=default_language or result.language or "en")
    result = apply_per_segment_language(result, default_language or result.language)
    if diarization_mode and diarization_mode.lower() not in {"off", "false", "0", "no"}:
        result = diarize(
            result, audio_path, mode=diarization_mode,
            num_speakers=speakers, hf_token=hf_token,
        )
    if corrections:
        result = apply_corrections(result, corrections)
    return result
