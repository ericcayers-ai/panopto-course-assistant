"""Versioned STT request/result types and capability flags."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 2
COMPAT_SCHEMA_VERSION = 1


class Profile(str, Enum):
    AUTO = "auto"
    QUALITY = "quality"
    FAST = "fast"
    LIVE = "live"
    ECO = "eco"


class TimingSource(str, Enum):
    NATIVE = "native"
    FORCED_ALIGN = "forced_align"
    CAPTION = "caption"
    NONE = "none"


class DiarizationSource(str, Enum):
    NONE = "none"
    PYANNOTE = "pyannote"
    ENGINE = "engine"


@dataclass
class Word:
    word: str
    start: float
    end: float
    confidence: Optional[float] = None
    speaker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "word": self.word,
            "start": float(self.start),
            "end": float(self.end),
        }
        if self.confidence is not None:
            d["confidence"] = float(self.confidence)
        if self.speaker:
            d["speaker"] = self.speaker
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Word":
        return cls(
            word=str(raw.get("word") or ""),
            start=float(raw.get("start", 0.0) or 0.0),
            end=float(raw.get("end", 0.0) or 0.0),
            confidence=_opt_float(raw.get("confidence")),
            speaker=raw.get("speaker") or None,
        )


@dataclass
class Segment:
    start: float
    end: float
    text: str
    id: Optional[int] = None
    speaker: Optional[str] = None
    language: Optional[str] = None
    confidence: Optional[float] = None
    words: List[Word] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Canonical segment dict; always includes start/end/text for v1 readers."""
        d: Dict[str, Any] = {
            "start": float(self.start),
            "end": float(self.end),
            "text": self.text,
        }
        if self.id is not None:
            d["id"] = int(self.id)
        if self.speaker:
            d["speaker"] = self.speaker
        if self.language:
            d["language"] = self.language
        if self.confidence is not None:
            d["confidence"] = float(self.confidence)
        if self.words:
            d["words"] = [w.to_dict() for w in self.words]
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], default_id: Optional[int] = None) -> "Segment":
        words_raw = raw.get("words") or []
        return cls(
            start=float(raw.get("start", 0.0) or 0.0),
            end=float(raw.get("end", 0.0) or 0.0),
            text=str(raw.get("text") or "").strip(),
            id=raw.get("id") if raw.get("id") is not None else default_id,
            speaker=raw.get("speaker") or None,
            language=raw.get("language") or None,
            confidence=_opt_float(raw.get("confidence")),
            words=[Word.from_dict(w) for w in words_raw if isinstance(w, dict)],
        )


@dataclass
class SpeakerTurn:
    speaker: str
    start: float
    end: float

    def to_dict(self) -> Dict[str, Any]:
        return {"speaker": self.speaker, "start": float(self.start), "end": float(self.end)}


@dataclass
class EngineCapabilities:
    batch: bool = True
    streaming: bool = False
    word_timestamps: bool = False
    language_id: bool = False
    diarization: bool = False
    keyword_bias: bool = False
    languages: List[str] = field(default_factory=list)  # empty = unknown / many
    offline: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HardwareInfo:
    cuda: bool = False
    cuda_via: Optional[str] = None
    gpu_name: str = ""
    vram_mb: int = 0
    compute_capability: Optional[str] = None
    ram_mb: int = 0
    cpu_count: int = 0
    free_disk_mb: int = 0
    ffmpeg: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class STTRequest:
    media_path: str = ""
    profile: str = Profile.AUTO.value
    language: str = "auto"
    engine: Optional[str] = None
    model: Optional[str] = None
    device: str = "auto"
    code_switch: bool = False
    word_timestamps: bool = True
    diarization: str = "auto"  # off | auto | on
    speakers: Optional[int] = None
    vocabulary: List[str] = field(default_factory=list)
    caption_first: bool = True
    resume: bool = True
    chunk_seconds: int = 180
    compute: str = "auto"  # auto | int8 | float16 | float32
    hotwords: str = ""
    initial_prompt: str = ""
    beam_size: int = 5
    vad_filter: bool = True
    caption_url: str = ""
    cookies: str = ""
    course: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "STTRequest":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in raw.items() if k in known}
        if "vocabulary" in kwargs and kwargs["vocabulary"] is None:
            kwargs["vocabulary"] = []
        return cls(**kwargs)


@dataclass
class RouteDecision:
    engine: str
    model: str
    profile: str
    reason: str
    fallbacks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class STTResult:
    segments: List[Segment] = field(default_factory=list)
    text: str = ""
    language: str = ""
    engine: str = ""
    model: str = ""
    device: str = ""
    schema_version: int = SCHEMA_VERSION
    route_reason: str = ""
    timing_source: str = TimingSource.NATIVE.value
    diarization_source: str = DiarizationSource.NONE.value
    engine_revision: str = ""
    input_fingerprint: str = ""
    preprocessing: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    speakers: List[SpeakerTurn] = field(default_factory=list)
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    fallbacks_used: List[str] = field(default_factory=list)
    raw_provenance: Dict[str, Any] = field(default_factory=dict)

    def numbered_segments(self) -> List[Segment]:
        out = []
        for i, seg in enumerate(self.segments, start=1):
            if seg.id is None:
                out.append(Segment(
                    start=seg.start, end=seg.end, text=seg.text, id=i,
                    speaker=seg.speaker, language=seg.language,
                    confidence=seg.confidence, words=list(seg.words),
                ))
            else:
                out.append(seg)
        return out

    def to_compat_segments(self) -> List[Dict[str, Any]]:
        """Segments always carrying start/end/text for legacy consumers."""
        return [s.to_dict() for s in self.numbered_segments()]

    def to_meta(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "model": self.model,
            "language": self.language,
            "device": self.device,
            "route_reason": self.route_reason,
            "timing_source": self.timing_source,
            "diarization_source": self.diarization_source,
            "engine_revision": self.engine_revision,
            "input_fingerprint": self.input_fingerprint,
            "preprocessing": self.preprocessing,
            "metrics": self.metrics,
            "fallbacks_used": list(self.fallbacks_used),
            "speakers": [s.to_dict() for s in self.speakers],
            "corrections": list(self.corrections),
            "raw_provenance": dict(self.raw_provenance),
        }

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Shape expected by write_outputs / existing job results."""
        return {
            "segments": self.to_compat_segments(),
            "text": self.text or " ".join(
                (s.text or "").strip() for s in self.segments if (s.text or "").strip()
            ).strip(),
            **self.to_meta(),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full schema-v2 dict (worker IPC + rich clients)."""
        return self.to_legacy_dict()

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "STTResult":
        return cls.from_legacy(raw)

    @classmethod
    def from_legacy(cls, raw: Dict[str, Any]) -> "STTResult":
        segs = [
            Segment.from_dict(s, default_id=i)
            for i, s in enumerate(raw.get("segments") or [], start=1)
            if isinstance(s, dict)
        ]
        speakers = [
            SpeakerTurn(
                speaker=str(s.get("speaker") or ""),
                start=float(s.get("start") or 0.0),
                end=float(s.get("end") or 0.0),
            )
            for s in (raw.get("speakers") or [])
            if isinstance(s, dict)
        ]
        return cls(
            segments=segs,
            text=str(raw.get("text") or ""),
            language=str(raw.get("language") or ""),
            engine=str(raw.get("engine") or ""),
            model=str(raw.get("model") or ""),
            device=str(raw.get("device") or ""),
            schema_version=int(raw.get("schema_version") or COMPAT_SCHEMA_VERSION),
            route_reason=str(raw.get("route_reason") or ""),
            timing_source=str(raw.get("timing_source") or TimingSource.NATIVE.value),
            diarization_source=str(
                raw.get("diarization_source") or DiarizationSource.NONE.value
            ),
            engine_revision=str(raw.get("engine_revision") or ""),
            input_fingerprint=str(raw.get("input_fingerprint") or ""),
            preprocessing=dict(raw.get("preprocessing") or {}),
            metrics=dict(raw.get("metrics") or {}),
            speakers=speakers,
            fallbacks_used=list(raw.get("fallbacks_used") or []),
            corrections=list(raw.get("corrections") or []),
            raw_provenance=dict(raw.get("raw_provenance") or {}),
        )


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
